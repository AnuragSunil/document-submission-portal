# Document Submission Portal — Complete Project Reference & Status

Technical reference, current status, architecture decisions and operations guide for the **Document Submission Portal** AWS capstone project.

---

## 1. Project Overview & Flow

Users upload documents (PDF, DOCX, PNG, JPG, max 10 MB).
- The file goes to a private Amazon S3 bucket (SSE-S3 `AES256`, HTTPS-only policy, Block Public Access).
- Metadata and status are stored in Amazon RDS PostgreSQL (`db.t4g.micro`, Single-AZ, encrypted).
- After upload the API sends a message to Amazon SQS and returns immediately. A Lambda consumes it and moves the document
  `UPLOADED` ➔ `PROCESSING` ➔ `PROCESSED` (or `FAILED`, with a retry action).
- The REST API is Python 3.12 (FastAPI + Uvicorn) on AWS ECS Fargate behind an Application Load Balancer.
- CI/CD builds and deploys through GitHub Actions using AWS IAM OIDC (no long-lived credentials).

```
User -> ALB (public subnets)
        -> ECS Fargate (public subnets, inbound only from alb-sg)
             |-> S3 documents bucket (private)
             |-> RDS PostgreSQL (private subnets)
             |-> SQS processing queue --(3 failures)--> Dead-letter queue --> CloudWatch alarm --> SNS
                    -> Lambda processor (private subnets) -> RDS
             |-> Secrets Manager (DB credentials)
             `-> CloudWatch Logs (/ecs/dsp-backend, /aws/lambda/dsp-processor; 7-day retention)
GitHub -> GitHub Actions (OIDC) -> ECR -> ECS rolling deployment;  Actions -> Lambda package -> S3 -> Lambda
```

Live URL: the `ALBDNSName` output of the `dsp-application` stack (HTTP).

---

## 2. Stacks (all `CREATE_COMPLETE` / `UPDATE_COMPLETE` once deployed)

| Stack | Template | Resources | Exports |
| :--- | :--- | :--- | :--- |
| `dsp-network` | [`infra/network.yaml`](infra/network.yaml) | VPC `10.0.0.0/16`, 2 public + 2 private subnets, IGW, route tables, `alb-sg`, `ecs-sg`, `rds-sg` | `dsp-network-*` |
| `dsp-cicd` | [`infra/cicd.yaml`](infra/cicd.yaml) | ECR `dsp-backend` (scan on push, keep 10), versioned artifacts bucket, GitHub OIDC provider, `dsp-github-deploy-role` | `dsp-cicd-ECRRepoUri`, `-ArtifactsBucketName`, `-GitHubDeployRoleArn` |
| `dsp-data` | [`infra/data.yaml`](infra/data.yaml) | Documents bucket, RDS PostgreSQL, Secrets Manager secret, SQS queue + DLQ, SNS alarm topic, app log group | `dsp-data-*` |
| `dsp-processing` | [`infra/processing.yaml`](infra/processing.yaml) | Processor Lambda, `lambda-sg`, Secrets Manager interface endpoint, event source mapping, DLQ + Lambda-error alarms | — |
| `dsp-application` | [`infra/application.yaml`](infra/application.yaml) | ECS cluster/service/task definition, ALB, target group, execution + task roles, 5xx and unhealthy-host alarms | `dsp-application-ALBDNSName` |

Deploy order: network, cicd, data, processing, application. Delete in reverse.

---

## 3. Key Decisions

### ECS in public subnets (cost vs. the reference design)
The reference architecture places Fargate in private subnets, which requires a NAT Gateway (~$32/month) or four interface
endpoints (~$29/month) so tasks can pull from ECR and reach S3, Logs and Secrets Manager. Tasks instead run in public
subnets with a public IP. This is **not an open door**: `ecs-sg` has a single inbound rule, TCP 8000 from `alb-sg`, so
nothing on the internet reaches a task directly. RDS stays private. To harden later, add NAT or endpoints and switch
`AssignPublicIp` to `DISABLED` and the service subnets to the private pair.

### Lambda in private subnets without NAT
The processor must reach RDS (private) and Secrets Manager. It uses one Secrets Manager interface endpoint (~$7/month,
single subnet) and a security group that only allows egress to RDS 5432 and that endpoint on 443.

### No Route 53 / HTTPS
The ALB DNS name over HTTP is the entry point. Adding a domain, an ACM certificate and an HTTPS listener is the next step
for production use.

### Cost summary (ap-south-1, approximate, running 24/7)
| Item | $/month |
| :--- | :--- |
| RDS `db.t4g.micro` + 20 GiB gp3 | ~12 |
| ALB | ~17 |
| Fargate 0.25 vCPU / 0.5 GB x 1 | ~9 |
| Secrets Manager endpoint (1 AZ) | ~7 |
| Secrets, SQS, Lambda, logs, S3 | ~1-2 |
| **Total** | **~46** |

---

## 4. Security Posture

- S3: Block Public Access on, SSE-S3, deny non-TLS. Downloads only through 60-second presigned URLs (regional SigV4).
- SQS: SSE enabled, deny non-TLS, DLQ with 14-day retention.
- RDS: private, encrypted, reachable only from `ecs-sg` and `lambda-sg`. Credentials generated in Secrets Manager; never in code or images.
- IAM: four separate roles (deploy, task execution, task, Lambda), each scoped to specific resources.
- OIDC: trust requires audience `sts.amazonaws.com` and a subject for this repository. GitHub currently sends
  `repo:<owner>@<ownerId>/<repo>@<repoId>:...`; the trust policy accepts that and the classic `repo:<owner>/<repo>:...` form.
- Input handling: extension + MIME allowlists, streamed 10 MB limit, UUID validation, parameterized SQL, safe error summaries; logs never contain file contents or credentials.
- Containers: non-root user (UID 1001).

**Known gaps**: no end-user authentication (anyone with the URL can upload and delete) and HTTP only.

---

## 5. Backend (`backend/`) and Processor (`processor/`)

- **API**: Python 3.12, FastAPI, Uvicorn, psycopg 3, boto3, Pydantic v2. Upload order: validate, S3 put, RDS insert (S3 object removed if the insert fails), enqueue to SQS. If `QUEUE_URL` is unset or SQS fails, processing falls back to in-process so uploads never break.
- **Processor**: [`processor/handler.py`](processor/handler.py) reads `{"document_id": ...}`, is idempotent, reports partial batch failures, and on the 3rd failed receive marks the document `FAILED` with a safe message. [`processor/build.py`](processor/build.py) builds a Linux x86_64 Python 3.12 package regardless of the build machine.
- **Tests**: `pytest backend/tests processor/tests` (23 tests total across API, validation, rollback, queue dispatch, fallback, edit/delete and the processor).

### REST endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Shallow 200 for ALB health checks |
| `GET` | `/ready` | Verifies RDS and S3 |
| `POST` | `/api/documents` | Multipart upload with validation, S3 + RDS + SQS |
| `GET` | `/api/documents` | List newest first; `search`, `status` filters |
| `GET` | `/api/documents/{id}` | One document's metadata |
| `PATCH` | `/api/documents/{id}` | Edit document type and description |
| `DELETE` | `/api/documents/{id}` | Delete record and S3 object |
| `GET` | `/api/documents/{id}/download` | 60-second presigned URL |
| `POST` | `/api/documents/{id}/retry` | Re-queue a `FAILED` document |
| `GET` | `/api/stats` | Counts by status |
| `GET` | `/` | Static single-page frontend |

---

## 6. CI/CD (`.github/workflows/test-build-deploy.yml`)

| Job | Trigger | Does |
| :--- | :--- | :--- |
| `validate-infra` | PR + push | `cfn-lint infra/*.yaml` |
| `test` | PR + push | `pytest backend/tests processor/tests` |
| `build-and-push` | push to `main` | OIDC role, ECR login, build, push tags `${{ github.sha }}` and `latest` |
| `package-processor` | push to `main` and `PROCESSOR_ENABLED=true` | Build Lambda zip, upload to artifacts bucket, `update-function-code` |
| `deploy` | push to `main` and `DEPLOY_ENABLED=true` | `ecs update-service --force-new-deployment`, wait for stable |

### Repository variables
`AWS_REGION=ap-south-1`, `ECR_REPOSITORY=dsp-backend`, `ROLE_TO_ASSUME=arn:aws:iam::773569338012:role/dsp-github-deploy-role`,
`DEPLOY_ENABLED=true`, `PROCESSOR_ENABLED=true` (set after `dsp-processing` is deployed).

### OIDC incident (resolved)
`Not authorized to perform sts:AssumeRoleWithWebIdentity` was caused by the trust policy only matching
`repo:AnuragSunil/document-submission-portal:*` while GitHub sent `repo:AnuragSunil@96289148/document-submission-portal@1442528262:ref:refs/heads/main`.
CloudTrail (`AssumeRoleWithWebIdentity` events) showed the real subject. The trust policy now accepts both forms with an exact audience.

---

## 7. Monitoring

| Alarm | Meaning |
| :--- | :--- |
| `dsp-alb-target-5xx` | 3+ backend 5xx responses in 5 minutes |
| `dsp-alb-unhealthy-hosts` | A task failed ALB health checks for 3 minutes |
| `dsp-processing-dlq-not-empty` | A document failed processing 3 times |
| `dsp-processor-lambda-errors` | Processor invocation errors |

All alarms notify SNS topic `dsp-alarms`. Pass `AlarmEmail=<address>` to the data stack to subscribe an email and confirm the message AWS sends.

---

## 8. Operations

Deploy, verify, safe updates and troubleshooting: [`docs/runbook.md`](docs/runbook.md). Teardown (ordered, including the
versioned artifacts bucket): [`docs/teardown.md`](docs/teardown.md). Architecture detail: [`docs/architecture.md`](docs/architecture.md).

Quick verification:

```powershell
$ALB = aws cloudformation describe-stacks --stack-name dsp-application --region ap-south-1 --query "Stacks[0].Outputs[?OutputKey=='ALBDNSName'].OutputValue" --output text
curl.exe "http://$ALB/health"; curl.exe "http://$ALB/ready"
aws logs tail /aws/lambda/dsp-processor --region ap-south-1 --since 5m
```

---

## 9. Capstone Requirements Coverage

| Area | Status |
| :--- | :--- |
| FR-01..FR-09 (dashboard, upload, S3, metadata, list/search/filter, details/download, status, health, timestamps) | Done |
| GitHub, GitHub Actions (OIDC), CloudFormation, ECR, ECS Fargate, ALB, VPC, RDS, S3, Secrets Manager, CloudWatch, IAM | Done |
| SQS + dead-letter queue, Lambda processor (extension) | Done |
| CloudWatch alarms | Done |
| Route 53 / HTTPS | Not done (cost; ALB DNS over HTTP) |
| Fargate in private subnets | Deviation: public subnets locked to the ALB, for cost (section 3) |
| AWS KMS customer-managed keys | Not done (optional; service-managed encryption used) |
