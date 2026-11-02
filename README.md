# Document Submission Portal

A cloud-native AWS solution for secure document upload, queued processing and status tracking, built with infrastructure-as-code and keyless CI/CD.

> Full technical reference, live status and operations commands: **[`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md)**.
> Architecture and design decisions: **[`docs/architecture.md`](docs/architecture.md)**.

**Live demo**: `http://<ALB DNS name>` (see the `dsp-application` stack output `ALBDNSName`).

---

## What it does

Users upload PDF, DOCX, PNG or JPG files (max 10 MB). The file is stored in a private S3 bucket, metadata goes to RDS PostgreSQL,
and an SQS message triggers a Lambda that moves the document through `UPLOADED` ➔ `PROCESSING` ➔ `PROCESSED` (or `FAILED`, with retry).
The web UI shows status counts, a searchable and filterable document list, download via short-lived presigned URL, and edit/delete.

```
User -> ALB -> ECS Fargate (FastAPI) -> S3 (files) + RDS (metadata) + SQS -> Lambda -> RDS (status)
GitHub -> GitHub Actions (OIDC) -> ECR -> ECS          Secrets Manager | CloudWatch | SNS alarms | IAM
```

## Repository structure

```
document-submission-portal/
├── frontend/                 # Single-page UI: dashboard, upload, list, edit, delete
├── backend/                  # FastAPI REST API
│   ├── main.py               # Endpoints, validation, SQS dispatch with in-process fallback
│   ├── db.py                 # PostgreSQL access, parameterized queries
│   ├── s3.py                 # S3 upload/delete, regional SigV4 presigned URLs
│   ├── sqs.py                # Enqueue processing messages
│   ├── config.py             # Environment + Secrets Manager loader
│   ├── tests/                # pytest suite
│   └── Dockerfile            # python:3.12-slim, non-root user
├── processor/                # SQS-triggered Lambda
│   ├── handler.py            # UPLOADED -> PROCESSING -> PROCESSED / FAILED
│   ├── build.py              # Builds processor/dist/processor.zip for Lambda (Linux, Python 3.12)
│   └── tests/
├── infra/
│   ├── network.yaml          # VPC, subnets, route tables, security groups
│   ├── cicd.yaml             # ECR, artifacts bucket, GitHub OIDC provider, deploy role
│   ├── data.yaml             # S3, RDS, Secrets Manager, SQS + DLQ, SNS alarm topic, log group
│   ├── processing.yaml       # Processor Lambda, Secrets endpoint, event source mapping, alarms
│   └── application.yaml      # ECS Fargate, ALB, task roles, ALB alarms
├── .github/workflows/
│   └── test-build-deploy.yml # cfn-lint, pytest, image build/push, Lambda package, ECS deploy
├── docs/
│   ├── architecture.md       # Diagram, flow, security groups, IAM, cost decisions
│   ├── runbook.md            # Deploy, verify, update safely, troubleshoot
│   └── teardown.md           # Ordered deletion
├── PROJECT_OVERVIEW.md
└── README.md
```

## Stacks (deploy in this order)

| Stack | Template | Provisions |
| :--- | :--- | :--- |
| `dsp-network` | `infra/network.yaml` | VPC `10.0.0.0/16`, 2 public + 2 private subnets, IGW, `alb-sg` / `ecs-sg` / `rds-sg` |
| `dsp-cicd` | `infra/cicd.yaml` | ECR (scan on push, keep 10 images), versioned artifacts bucket, GitHub OIDC provider, deploy role |
| `dsp-data` | `infra/data.yaml` | Private S3 bucket (SSE-S3, HTTPS only), RDS PostgreSQL `db.t4g.micro`, Secrets Manager, SQS queue + DLQ, SNS topic, log group |
| `dsp-processing` | `infra/processing.yaml` | Lambda in private subnets, Secrets Manager interface endpoint, `lambda-sg`, SQS event source mapping, DLQ and error alarms |
| `dsp-application` | `infra/application.yaml` | ECS cluster/service/task definition, ALB + target group, task roles, 5xx and unhealthy-host alarms |

## Key design decisions

- **ECS tasks run in public subnets** to avoid NAT Gateway / VPC endpoint cost (~$60/month). They are not exposed: `ecs-sg` allows inbound 8000 **only from the ALB**. RDS and the Lambda are in private subnets. Details in [`docs/architecture.md`](docs/architecture.md).
- **No long-lived AWS keys**: GitHub Actions assumes `dsp-github-deploy-role` through OIDC (audience and repository-scoped subject).
- **Separate deploy and runtime permissions**: deploy role, task execution role, task role and Lambda role each hold only what they need.
- **Upload never waits for processing**: the API returns after queuing. If SQS is unavailable the API falls back to in-process processing.
- **Failures are visible**: 3 failed attempts move a message to the DLQ, mark the document `FAILED`, and raise a CloudWatch alarm.

## API

| Method | Path | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health`, `/ready` | Liveness for the ALB; readiness checks RDS and S3 |
| `POST` | `/api/documents` | Multipart upload (`file`, `document_type`, `description`) |
| `GET` | `/api/documents` | List, newest first; `search` and `status` filters |
| `GET` | `/api/documents/{id}` | Metadata for one document |
| `PATCH` | `/api/documents/{id}` | Edit type and description |
| `DELETE` | `/api/documents/{id}` | Delete record and S3 object |
| `GET` | `/api/documents/{id}/download` | 60-second presigned URL |
| `POST` | `/api/documents/{id}/retry` | Re-queue a `FAILED` document |
| `GET` | `/api/stats` | Counts by status |

## Local development and tests

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r backend/requirements.txt -r backend/requirements-dev.txt

.\.venv\Scripts\pytest backend/tests processor/tests   # unit tests
cfn-lint infra/*.yaml                                  # template validation
.\.venv\Scripts\python processor/build.py              # build the Lambda package
```

## Deploy, operate, tear down

- Deploy and verify: [`docs/runbook.md`](docs/runbook.md)
- Tear down all resources: [`docs/teardown.md`](docs/teardown.md)

## Known limitations

HTTP only (no Route 53 / ACM), no user authentication, simulated processing. See [`docs/architecture.md`](docs/architecture.md).
