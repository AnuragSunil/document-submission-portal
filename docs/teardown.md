# Teardown Guide

To clean up resources provisioned by the CloudFormation stacks, follow reverse order of creation:

### 1. Delete Application Stack (When deployed)
```bash
aws cloudformation delete-stack --stack-name dsp-application
aws cloudformation wait stack-delete-complete --stack-name dsp-application
```

### 2. Delete Data Stack (When deployed)
```bash
aws cloudformation delete-stack --stack-name dsp-data
aws cloudformation wait stack-delete-complete --stack-name dsp-data
```

### 3. Delete Network Stack
```bash
aws cloudformation delete-stack --stack-name dsp-network
aws cloudformation wait stack-delete-complete --stack-name dsp-network
```
