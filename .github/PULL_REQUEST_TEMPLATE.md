## What changed

<!-- A short summary. Link the issue if there is one. -->

## How to test

- [ ] `cd backend && python -m pytest -q`
- [ ] `cd frontend && npm run lint && npm run build`
- [ ] `make helm-lint` (if you touched `deploy/helm`)
- [ ] `terraform -chdir=deploy/terraform fmt -check && terraform -chdir=deploy/terraform validate` (if you touched Terraform)

## Notes

<!-- Anything a reviewer should watch for? Breaking changes, skipped checks, follow-ups. -->
