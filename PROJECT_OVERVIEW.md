# Document Submission Portal — Complete Project Reference & Status

This document contains the complete technical blueprint, current progress across all stages, architecture details, commands, and operations guide for the **Document Submission Portal** AWS capstone project.

---

## 1. Project Overview & Target Flow

Users upload documents (PDF, DOCX, PNG, JPG, max 10 MB).
- File goes to a private Amazon S3 bucket with default SSE-S3 (`AES256`) encryption and HTTPS-only transport enforcement.
- Document metadata and status are stored in an Amazon RDS PostgreSQL database (`db.t4g.micro`, Single-AZ).
- Status workflow: `UPLOADED` ➔ `PROCESSING` ➔ `PROCESSED` (or `FAILED` with retry option).
- Backend REST API is built with Python 3.12 (FastAPI + Uvicorn) running on AWS ECS Fargate behind an Application Load Balancer (ALB).
- CI/CD builds container images and manages zero-downtime rolling deployments via GitHub Actions using AWS IAM OIDC (no long-lived credentials).

```
[ User / Client ]
       │
       ▼ (HTTP Port 80 / ALB DNS Name)
[ Application Load Balancer ] (Public Subnets, alb-sg)
       │
       ▼ (Port 8000)
[ ECS Fargate Tasks ] (Public Subnets with AssignPublicIp: ENABLED, ecs-sg)
   ├──▶ S3 Bucket (Private document storage, HTTPS-only)
   ├──▶ RDS PostgreSQL (Private Subnets, rds-sg, Port 5432)
   ├──▶ Secrets Manager (Database master credentials)
   └──▶ CloudWatch Logs (/ecs/dsp-backend, 7-day retention)
```

---

## 2. Infrastructure Architecture & Stacks Breakdown

All infrastructure is provisioned through modular CloudFormation templates in [`infra/`](infra/):

```
infra/
├── network.yaml      # VPC, subnets, route tables, tiered security groups
├── data.yaml         # S3 bucket, RDS PostgreSQL, Secrets Manager, CloudWatch Logs
├── cicd.yaml         # ECR repository, GitHub OIDC provider, Deploy IAM role
└── application.yaml  # ECS Cluster, ALB, Task Definition, Fargate Service
```

### Stacks Summary

| Stack Name | CloudFormation Template | Resources Provisioned | Key Exports | Status |
| :--- | :--- | :--- | :--- | :--- |
| **`dsp-network`** | [`infra/network.yaml`](infra/network.yaml:1) | - 1 VPC (`10.0.0.0/16`) across 2 AZs<br>- 2 Public Subnets (`10.0.1.0/24`, `10.0.2.0/24`)<br>- 2 Private Subnets (`10.0.11.0/24`, `10.0.12.0/24`)<br>- 1 Internet Gateway & Route Tables<br>- 3 Tiered Security Groups (`alb-sg`, `ecs-sg`, `rds-sg`) | `dsp-network-VPCID`<br>`dsp-network-PublicSubnets`<br>`dsp-network-PrivateSubnets`<br>`dsp-network-ALBSecurityGroup`<br>`dsp-network-ECSSecurityGroup`<br>`dsp-network-RDSSecurityGroup` | **DEPLOYED (`CREATE_COMPLETE`)** |
| **`dsp-data`** | [`infra/data.yaml`](infra/data.yaml:1) | - S3 Documents Bucket (SSE-S3, HTTPS-only policy)<br>- CloudWatch Log Group (`/ecs/dsp-backend`, 7d retention)<br>- Secrets Manager DB Secret (`GenerateSecretString`)<br>- DB Subnet Group on private subnets<br>- RDS PostgreSQL (`db.t4g.micro`, 20GB gp3, encrypted)<br>- SecretTargetAttachment linking DB to Secret | `dsp-data-DocumentsBucketName`<br>`dsp-data-DocumentsBucketArn`<br>`dsp-data-DBSecretArn`<br>`dsp-data-DBEndpoint`<br>`dsp-data-AppLogGroupName` | **Template Ready / Change Set Created** |
| **`dsp-cicd`** | [`infra/cicd.yaml`](infra/cicd.yaml:1) | - ECR Repository (`dsp-backend`, ScanOnPush, Keep 10 images)<br>- IAM OIDC Provider (`token.actions.githubusercontent.com`)<br>- IAM Deploy Role (`dsp-github-deploy-role`) | `dsp-cicd-ECRRepoUri`<br>`dsp-cicd-GitHubDeployRoleArn` | **DEPLOYED (`CREATE_COMPLETE`)** |
| **`dsp-application`** | [`infra/application.yaml`](infra/application.yaml:1) | - ECS Cluster (`dsp-cluster`)<br>- ALB (internet-facing, public subnets)<br>- Target Group (port 8000, `/health` probe)<br>- HTTP 80 Listener<br>- Task Execution Role (`AmazonECSTaskExecutionRolePolicy`)<br>- Task Role (scoped S3 + Secrets Manager access)<br>- Task Definition (Fargate, 256 CPU / 512 RAM)<br>- ECS Fargate Service (Rolling updates, rollback circuit breaker) | `dsp-application-ALBDNSName` | **Template Ready for Deploy** |

---

## 3. Cost Optimization Decisions

1. **Zero NAT Gateway / Zero VPC Endpoints Architecture**:
   - Running ECS tasks in public subnets with `AssignPublicIp: ENABLED` allows outbound internet access to ECR, S3, Secrets Manager, and CloudWatch without costly NAT Gateways or Interface VPC Endpoints.
   - **Cost Savings**: Saves ~$32/month per NAT Gateway and ~$29.20/month for 4 VPC Endpoints.
   - **Security**: The `ecs-sg` security group **strictly rejects all inbound traffic from the internet**, permitting ingress on port 8000 only from `alb-sg`.
2. **Cheapest Safe RDS**:
   - `db.t4g.micro` (AWS Graviton2) at ~$11.68/month in `ap-south-1`.
   - Single-AZ, 20 GiB `gp3` storage, automated 1-day backups.
3. **No KMS Customer Keys / No Route 53**:
   - Standard SSE-S3 (`AES256`) and default RDS encryption.
   - Access via ALB Public DNS name over HTTP.

---

## 4. Backend Application (`backend/`)

- **Language & Framework**: Python 3.12, FastAPI, Uvicorn, psycopg3, boto3, Pydantic v2.
- **Containerization**: [`backend/Dockerfile`](backend/Dockerfile:1) based on `python:3.12-slim` running under non-root `appuser` (UID 1001).
- **Automated Tests**: [`backend/tests/test_api.py`](backend/tests/test_api.py:1) with 13 unit tests covering health, readiness, MIME allowlists, 10MB chunked size limits, S3 deletion rollback on DB error, parameterized search, and retry flow.

### REST API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Shallow HTTP 200 health check for ALB target groups (no dependencies). |
| `GET` | `/ready` | Deep readiness probe verifying RDS database and S3 reachability. |
| `POST` | `/api/documents` | Multipart document upload (`file`, `document_type`, `description`). Enforces MIME/extension validation and 10MB streaming limit. Uploads to S3, inserts RDS metadata, and triggers background processing. |
| `GET` | `/api/documents` | List documents ordered newest first. Query parameters: `search` (case-insensitive) and `status` (`UPLOADED`, `PROCESSING`, `PROCESSED`, `FAILED`). |
| `GET` | `/api/documents/{id}` | Retrieve single document metadata by UUID. |
| `GET` | `/api/documents/{id}/download` | Generates a 60-second presigned S3 download URL as JSON `{"url": "..."}` with sanitized `Content-Disposition`. |
| `GET` | `/api/stats` | Aggregated document counts by status (`total`, `UPLOADED`, `PROCESSING`, `PROCESSED`, `FAILED`). |
| `POST` | `/api/documents/{id}/retry` | Resets a `FAILED` document back to `UPLOADED` and re-triggers background processing. |
| `GET` | `/*` | Static frontend files mounted from `frontend/` directory (fallback). |

---

## 5. CI/CD Pipeline (`.github/workflows/test-build-deploy.yml`)

- **Authentication**: Keyless AWS IAM OIDC via `aws-actions/configure-aws-credentials@v4`.
- **Jobs**:
  1. `validate-infra`: Runs `cfn-lint infra/*.yaml` on every PR and push.
  2. `test`: Installs dependencies and runs `pytest backend/tests` on every PR and push.
  3. `build-and-push`: Runs on push to `main`. Assumes `dsp-github-deploy-role`, logs into ECR, builds Docker image, and pushes tags `${{ github.sha }}` and `latest`.
  4. `deploy`: Runs on push to `main` when repository variable `DEPLOY_ENABLED == 'true'`, executing `aws ecs update-service --force-new-deployment`.

### Required GitHub Repository Variables
Configure in **GitHub Repo ➔ Settings ➔ Secrets and variables ➔ Actions ➔ Variables**:
- `AWS_REGION`: `ap-south-1`
- `ECR_REPOSITORY`: `dsp-backend`
- `ROLE_TO_ASSUME`: `arn:aws:iam::773569338012:role/dsp-github-deploy-role`
- `DEPLOY_ENABLED`: `false` (set to `true` after application stack is deployed)

---

## 6. Complete Deployment Runbook (Copy-Paste Commands)

### 1. Update/Deploy CI/CD Stack (ECR & OIDC)
```powershell
aws cloudformation deploy `
  --template-file infra/cicd.yaml `
  --stack-name dsp-cicd `
  --parameter-overrides EnvironmentName=dsp GitHubOrgRepo=AnuragSunil/document-submission-portal `
  --tags Project=dsp `
  --capabilities CAPABILITY_NAMED_IAM
```

### 2. Verify First ECR Container Image Pushed
```powershell
aws ecr describe-images --repository-name dsp-backend --region ap-south-1 --query "imageDetails[*].[imageTags,imagePushedAt]" --output table
```

### 3. Execute `dsp-data` Change Set (Starts RDS Billing)
```powershell
aws cloudformation execute-change-set `
  --stack-name dsp-data `
  --change-set-name $(aws cloudformation list-change-sets --stack-name dsp-data --query "Summaries[0].ChangeSetName" --output text)
```

### 4. Deploy `dsp-application` Stack
```powershell
aws cloudformation deploy `
  --template-file infra/application.yaml `
  --stack-name dsp-application `
  --parameter-overrides EnvironmentName=dsp ImageTag=latest DesiredCount=1 `
  --tags Project=dsp `
  --capabilities CAPABILITY_NAMED_IAM
```

### 5. Verify ALB & Health Checks
```powershell
$ALB_DNS = aws cloudformation describe-stacks --stack-name dsp-application --query "Stacks[0].Outputs[?OutputKey=='ALBDNSName'].OutputValue" --output text
Write-Host "Application URL: http://$ALB_DNS"
curl "http://$ALB_DNS/health"
curl "http://$ALB_DNS/ready"
```

---

## 7. Teardown Procedure

To cleanly delete all resources without leaving orphan charges:

```powershell
# 1. Delete Application Stack
aws cloudformation delete-stack --stack-name dsp-application
aws cloudformation wait stack-delete-complete --stack-name dsp-application

# 2. Empty and Delete Data Stack (S3 + RDS)
$BUCKET = aws cloudformation describe-stacks --stack-name dsp-data --query "Stacks[0].Outputs[?OutputKey=='DocumentsBucketName'].OutputValue" --output text
aws s3 rm "s3://$BUCKET" --recursive
aws cloudformation delete-stack --stack-name dsp-data
aws cloudformation wait stack-delete-complete --stack-name dsp-data

# 3. Delete CI/CD Stack
aws cloudformation delete-stack --stack-name dsp-cicd
aws cloudformation wait stack-delete-complete --stack-name dsp-cicd

# 4. Delete Base Network Stack
aws cloudformation delete-stack --stack-name dsp-network
aws cloudformation wait stack-delete-complete --stack-name dsp-network
```
