# Teardown Guide

Delete in reverse dependency order. Run from the repository root in PowerShell. Region `ap-south-1`.

```
dsp-application  ->  dsp-processing  ->  dsp-data  ->  dsp-cicd  ->  dsp-network
```

Billing stops for the major cost items (RDS, ALB, Fargate, Secrets Manager endpoint) as soon as their stacks are deleted.

```powershell
$R = "ap-south-1"

# 1. Application stack (ECS service, ALB, alarms). Frees the Fargate and ALB charges first.
aws cloudformation delete-stack --stack-name dsp-application --region $R
aws cloudformation wait stack-delete-complete --stack-name dsp-application --region $R

# 2. Processing stack (Lambda, Secrets Manager VPC endpoint, event source mapping, alarms)
aws cloudformation delete-stack --stack-name dsp-processing --region $R
aws cloudformation wait stack-delete-complete --stack-name dsp-processing --region $R

# 3. Data stack. The documents bucket must be empty first, or the delete fails.
$BUCKET = aws cloudformation describe-stacks --stack-name dsp-data --region $R `
  --query "Stacks[0].Outputs[?OutputKey=='DocumentsBucketName'].OutputValue" --output text
aws s3 rm "s3://$BUCKET" --recursive --region $R
aws cloudformation delete-stack --stack-name dsp-data --region $R
aws cloudformation wait stack-delete-complete --stack-name dsp-data --region $R

# 4. CI/CD stack. The artifacts bucket is versioned: delete every version and delete marker first.
$ACCOUNT = aws sts get-caller-identity --query Account --output text
$ART = "dsp-artifacts-$ACCOUNT-$R"
$versions = aws s3api list-object-versions --bucket $ART --region $R --output json | ConvertFrom-Json
$objects = @($versions.Versions) + @($versions.DeleteMarkers) | Where-Object { $_ } |
  ForEach-Object { @{ Key = $_.Key; VersionId = $_.VersionId } }
if ($objects.Count -gt 0) {
  $payload = @{ Objects = $objects; Quiet = $true } | ConvertTo-Json -Depth 5 -Compress
  $payload | Set-Content -Path delete-versions.json -Encoding ascii
  aws s3api delete-objects --bucket $ART --delete file://delete-versions.json --region $R
  Remove-Item delete-versions.json
}
aws cloudformation delete-stack --stack-name dsp-cicd --region $R
aws cloudformation wait stack-delete-complete --stack-name dsp-cicd --region $R

# 5. Network stack
aws cloudformation delete-stack --stack-name dsp-network --region $R
aws cloudformation wait stack-delete-complete --stack-name dsp-network --region $R
```

## After teardown

- Confirm nothing remains: `aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --region ap-south-1`.
- RDS takes no final snapshot (`DeletionProtection: false`, no snapshot policy), so data is gone.
- Set `DEPLOY_ENABLED` and `PROCESSOR_ENABLED` repository variables to `false`, otherwise pushes to `main` will try to
  deploy to resources that no longer exist.
- The ECR repository is deleted with its images (`EmptyOnDelete: true`).
- The GitHub OIDC provider is removed with `dsp-cicd`. Delete it manually only if it was created outside CloudFormation.
