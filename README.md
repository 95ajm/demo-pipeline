# demo-pipeline

A working CI/CD pipeline demonstrating automated build, test, and deployment of a containerized Python application to a local KIND cluster using GitHub Actions, Helm, and ArgoCD.

## What This Project Demonstrates

A demo GitOps delivery pipeline:

- A minimal Python Flask application serving a string and a health endpoint
- A CI pipeline that lints, tests, builds, and pushes a Docker image to GitHub Container Registry on every merge to `main`
- A Helm chart packaging the application for Kubernetes deployment
- A CD pipeline that updates the Helm values file with the new image tag and commits back to the repository
- ArgoCD running in a local kind cluster that detects the values change and reconciles the cluster automatically
- Some things that I considered best practices, and other things to show some optionality available in GitHub Actions

## How to Run Locally

### Prerequisites

- Docker
- kind
- kubectl
- helm
- argocd CLI

### Setup

```bash
# Create local cluster
kind create cluster --name demo

# Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for ArgoCD pods to be ready
kubectl get pods -n argocd -w

# Apply the ArgoCD Application manifest
kubectl apply -f argocd/application.yaml

# Get ArgoCD initial admin password
kubectl get secret argocd-initial-admin-secret -n argocd \
  -o jsonpath="{.data.password}" | base64 -d

# Port-forward ArgoCD and log in
kubectl port-forward svc/argocd-server -n argocd 8080:443
argocd login localhost:8080 --username admin --insecure

# Port-forward the app
kubectl port-forward svc/demo-pipeline 9090:80

# Verify
curl http://localhost:9090          # Hello World
curl http://localhost:9090/health   # {"status": "ok", "timestamp": "..."}
```

## Design Decisions

### Workflow structure - separate files with CI as orchestrator

Lint, test, and build each live in their own reusable workflow file with both `pull_request` and `workflow_call` triggers. On a pull request all three run, lint and test validate the code, build verifies the Dockerfile compiles without pushing an image. On merge to main, `ci.yml` calls all three in sequence using `needs:` dependencies and the build job pushes the image to GHCR. CD lives in its own separate workflow triggered by `workflow_run` when CI completes successfully - surfacing CI and CD as distinct entries in the Actions tab so a CD failure is clearly attributable rather than appearing as a CI failure.

### Push to GHCR on merge to main only

The build workflow runs on every pull request to verify the Dockerfile compiles cleanly. The login and push steps are gated on `github.ref == 'refs/heads/main'` - on a PR the ref is the feature branch so no image is published. On merge to main the condition is true and the image is pushed to GHCR.

### CD via workflow_run rather than workflow_call

CD is triggered by `workflow_run` watching for CI to complete successfully. This surfaces CI and CD as separate entries in the GitHub Actions tab so if CD fails it's clearly a CD failure, not a CI failure. The CD summary links back to the triggering CI run URL for full traceability in both directions.

### Stale ref pinning

CD checks out the exact SHA that CI built using `github.event.workflow_run.head_sha` rather than `ref: main`. This prevents a race condition where a newer commit merges between CI completing and CD checking out which would cause a mismatch between the deployed image tag and the code in the repo.

### [skip ci] on the CD commit

When CD commits the updated image tag to values.yaml it appends `[skip ci]` to the commit message. GitHub recognizes this and skips workflow triggers for that commit, preventing an infinite loop where the CD commit re-triggers CI which re-triggers CD.

### Concurrency group on deploy job

The CD deploy job uses a concurrency group with `cancel-in-progress: false`. If two CI runs complete close together and both trigger CD, the second deploy queues rather than running simultaneously. This prevents both jobs from checking out and writing values.yaml at the same time, which would cause a non-fast-forward push failure on one of them.

### GitHub environment approval gate

The CD deploy job references a GitHub environment named `production` with a required reviewer. Deployment pauses until a human approves, a stub for the manual promotion gate you would use between staging and production environments. Whether an approval gate is appropriate depends on team workflow, environment and SDLC, but I wanted to stub it out to demonstrate the capability.

### Liveness and readiness probes

The Helm deployment template includes liveness and readiness probes pointing at the `/health` endpoint. The readiness probe prevents Kubernetes from routing traffic to a new pod until it is actually ready. The liveness probe restarts the pod if it becomes unresponsive after startup. This is why the health endpoint exists in the Flask app.

### Alpine base image

The Dockerfile uses `python:3.11-alpine` rather than a full Debian base. Alpine provides significantly smaller images with a reduced attack surface.

## What I Would Add With More Time

### Separate app and ops repositories

This demo uses a single repository for simplicity, with more time I would split the application code and deployment configuration into separate repositories. The app repo containing source code, Dockerfile, and CI, and an Ops repo containing Helm charts, ArgoCD manifests, and values files. CI in the app repo builds and pushes an image then opens a PR against the ops repo to update the image tag. This keeps deployment history clean, access control separate, and aligns with ArgoCD's recommended pattern.

### Real tests

The test workflow is stubbed with echo statements. With more time I would add pytest covering at minimum:

- `GET /` returns HTTP 200 and body `Hello World`
- `GET /health` returns HTTP 200 and a JSON body containing 
  `"status": "ok"`

### Post-deployment observability and rollback

Currently the pipeline uses ArgoCD's automated sync policy, CD commits the image tag update and ArgoCD reconciles the cluster without an explicit trigger. Adding an explicit sync and health wait would allow the pipeline to gate post-deploy steps on actual cluster health rather than fire and forget:

```bash
argocd app sync demo-pipeline
argocd app wait demo-pipeline --health --timeout 300
```

After a confirmed healthy sync, smoke tests would curl the service endpoint and assert correct status codes and response bodies. On failure, an automatic rollback would revert to the last known-good revision:

```bash
argocd app rollback demo-pipeline
```

### Security hardening

- Non-root container user in the Dockerfile
- Pinned versions in requirements.txt for reproducible builds
- Pinning GitHub Actions to a SHA versus a version
- OIDC secrets retrieval if it were applicable
- Trivy or grype scanning that fails on certain vulnerability thresholds

### Multi-environment promotion

Separate ArgoCD Application manifests for staging and production pointing at the same Helm chart with environment-specific values files (`values-staging.yaml`, `values-production.yaml`). The GitHub environment approval gate on the production CD job handles human-gated promotion between environments.

## What I Learned & Issues Encountered

### CrashLoopBackOff Troubleshooting

When installing ArgoCD locally, I was getting a CrashLoopBackOff on an Argo pod.Trying to describe or get logs by name fails with "not found" because the pod was already replaced. The correct approach is to use a label selector with `--previous` to see the last crash logs and diagnose accordingly.

### Pod Unknown state after machine restart

Returning to the cluster after a machine sleep or reboot can leave pods in `Unknown` state — the Kubernetes API server lost contact with the node and cannot determine pod status. Force deleting the affected pod allows Kubernetes to recreate it cleanly:

```bash
kubectl delete pod <pod-name> -n <namespace> --force --grace-period=0
```

### [skip ci] and infinite loop prevention

When a CD workflow commits back to the repository it re-triggers CI unless explicitly prevented. GitHub recognizes `[skip ci]` in the commit message and skips workflow triggers for that commit.

### Race conditions in GitOps commit-back patterns

When multiple CI runs complete close together, multiple CD runs can trigger simultaneously and both attempt to update `values.yaml`. Two mitigations applied here:

1. Concurrency group with `cancel-in-progress: false` - queues deploys rather than running them in parallel
2. Checkout pinned to `github.event.workflow_run.head_sha` - each CD run writes the SHA of the commit that triggered it, not whatever HEAD currently is

## Resources Used

- Claude - used for researching, planning and debugging
- ArgoCD documentation - Application manifest structure, automated sync policy, app wait flags
- Helm documentation - chart structure, values templating
- GitHub Actions documentation - workflow_run trigger, concurrency groups, GITHUB_OUTPUT syntax, job summaries
- kind documentation - local cluster setup
- various online forums, StackOverflow