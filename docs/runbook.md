# Runbook

All commands are PowerShell, run from the repository root. Region: `ap-south-1`.

## Stack order and dependencies

```
dsp-network  ->  dsp-cicd  ->  dsp-data  ->  dsp-processing  ->  dsp-application
```

Stacks import each other's outputs (`dsp-network-*`, `dsp-cicd-*`, `dsp-data-*`), so deploy in this order and delete in
reverse. `dsp-application` and `dsp-processing` both need `dsp-data`.

## 1. First-time deployment

```powershell
$R = "ap-south-1"

# 1. Network (VPC, subnets, security groups)
aws cloudformation deploy --template-file infra/network.yaml --stack-name dsp-network `
  --parameter-overrides EnvironmentName=dsp --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM --region $R

# 2. CI/CD (ECR, artifacts bucket, GitHub OIDC provider + deploy role)
aws cloudformation deploy --template-file infra/cicd.yaml --stack-name dsp-cicd `
  --parameter-overrides EnvironmentName=dsp GitHubOrgRepo=AnuragSunil/document-submission-portal `
  --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM --region $R

# 3. Data (S3, RDS, Secrets Manager, SQS + DLQ, SNS alarm topic). RDS takes ~5-10 minutes.
#    AlarmEmail is optional; confirm the subscription email AWS sends you.
aws cloudformation deploy --template-file infra/data.yaml --stack-name dsp-data `
  --parameter-overrides EnvironmentName=dsp AlarmEmail=you@example.com `
  --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM --region $R
```

Push the backend image first (merge to `main`; the `build-and-push` job does it). Verify:

```powershell
aws ecr describe-images --repository-name dsp-backend --region $R `
  --query "imageDetails[*].{Tags:join(',',imageTags),Pushed:imagePushedAt}" --output table
```

Build and upload the Lambda package, then deploy the processing and application stacks:

```powershell
# 4. Package the processor Lambda and upload it to the artifacts bucket
.\.venv\Scripts\python.exe processor/build.py
$ACCOUNT = aws sts get-caller-identity --query Account --output text
aws s3 cp processor/dist/processor.zip "s3://dsp-artifacts-$ACCOUNT-$R/processor/processor.zip" --region $R

# 5. Processing (Lambda in private subnets, Secrets Manager endpoint, event source mapping, alarms)
aws cloudformation deploy --template-file infra/processing.yaml --stack-name dsp-processing `
  --parameter-overrides EnvironmentName=dsp --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM --region $R

# 6. Application (ECS, ALB, task definition, alarms)
aws cloudformation deploy --template-file infra/application.yaml --stack-name dsp-application `
  --parameter-overrides EnvironmentName=dsp ImageTag=latest DesiredCount=1 `
  --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM --region $R
```

Enable automatic Lambda deployment from CI once `dsp-processing` exists:

```powershell
gh variable set PROCESSOR_ENABLED --body true
gh variable set DEPLOY_ENABLED --body true
```

### Required GitHub repository variables

| Variable | Value |
| :--- | :--- |
| `AWS_REGION` | `ap-south-1` |
| `ECR_REPOSITORY` | `dsp-backend` |
| `ROLE_TO_ASSUME` | `arn:aws:iam::773569338012:role/dsp-github-deploy-role` |
| `DEPLOY_ENABLED` | `true` after `dsp-application` is deployed |
| `PROCESSOR_ENABLED` | `true` after `dsp-processing` is deployed |

## 2. Verify

```powershell
$ALB = aws cloudformation describe-stacks --stack-name dsp-application --region $R `
  --query "Stacks[0].Outputs[?OutputKey=='ALBDNSName'].OutputValue" --output text
curl.exe "http://$ALB/health"      # {"status":"ok"}
curl.exe "http://$ALB/ready"       # {"status":"ready"}  (checks RDS + S3)
Start-Process "http://$ALB"        # web UI
```

Confirm the queue path works: upload a file in the UI, then it should reach `PROCESSED` within a few seconds, and
the Lambda log shows `document_processed`:

```powershell
aws logs tail /aws/lambda/dsp-processor --region $R --since 5m
aws sqs get-queue-attributes --queue-url (aws cloudformation describe-stacks --stack-name dsp-data --region $R `
  --query "Stacks[0].Outputs[?OutputKey=='ProcessingQueueUrl'].OutputValue" --output text) `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region $R
```

## 3. Safe update procedure (Change Sets)

Always preview changes to stateful stacks (`dsp-data`, `dsp-network`) before applying them.

```powershell
# Preview only
aws cloudformation deploy --template-file infra/data.yaml --stack-name dsp-data `
  --parameter-overrides EnvironmentName=dsp --tags Project=dsp --capabilities CAPABILITY_NAMED_IAM `
  --no-execute-changeset --region $R

# Inspect
$CS = aws cloudformation list-change-sets --stack-name dsp-data --region $R `
  --query "Summaries[?Status=='CREATE_COMPLETE']|[0].ChangeSetName" --output text
aws cloudformation describe-change-set --stack-name dsp-data --change-set-name $CS --region $R `
  --query "Changes[*].ResourceChange.[Action,Replacement,LogicalResourceId,ResourceType]" --output table

# Apply (only if no stateful resource shows Replacement: True)
aws cloudformation execute-change-set --stack-name dsp-data --change-set-name $CS --region $R
```

> **Safety check**: do not execute if `DBInstance`, `VPC`, subnets or security groups show `Replacement: True`.

## 4. Troubleshooting

| Symptom | Check |
| :--- | :--- |
| Workflow fails with `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Look up the real subject claim with `aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity --max-results 1 --region ap-south-1`. It must match a pattern in the role trust policy (`dsp-cicd` parameters `GitHubOrgRepo`, `GitHubOwnerId`, `GitHubRepoId`). |
| `/ready` returns 503 | `aws logs tail /ecs/dsp-backend --since 15m`. Usually the task role, secret ARN or the RDS security group. |
| Document stuck in `UPLOADED` | Messages are queued but the Lambda is not consuming. Check `dsp-processing` exists and `aws lambda get-event-source-mapping`. Check Lambda logs. |
| Lambda times out connecting | The function must be in the private subnets with `lambda-sg`; `rds-sg` must allow 5432 from `lambda-sg`; the Secrets Manager endpoint must be `available`. |
| Document `FAILED` | Look in the DLQ (below), fix the cause, then use **Retry** in the UI. |
| Download fails with `SignatureDoesNotMatch` | The S3 client must use the regional endpoint with SigV4 (`backend/s3.py`). |
| Alarm fired | See `docs/architecture.md` (Monitoring) for what each alarm means. |

Inspect the dead-letter queue:

```powershell
$DLQ = aws sqs get-queue-url --queue-name dsp-processing-dlq --region $R --query QueueUrl --output text
aws sqs receive-message --queue-url $DLQ --max-number-of-messages 5 --region $R
```

## 5. Teardown

See [`teardown.md`](teardown.md).
