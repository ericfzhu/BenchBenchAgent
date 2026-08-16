# Quota project-resource resolution

The Service Usage quota endpoint identifies a project with its numeric project
resource, for example `projects/123456789012`. BBA continues to freeze and show
the human-readable project ID, but resolves that ID to the numeric resource
before reading `aiplatform.googleapis.com` quota metrics.

By default the quota reader calls Cloud Resource Manager with the active ADC
credentials. The principal therefore needs `resourcemanager.projects.get` in
addition to `serviceusage.quotas.get`.

When project metadata cannot be read, supply the number explicitly:

```bash
export BBA_GCP_PROJECT_NUMBER="123456789012"
```

`GOOGLE_CLOUD_PROJECT_NUMBER` is also accepted. BBA validates the configured
value and still uses `GOOGLE_CLOUD_PROJECT` as the frozen experiment project ID.
The project-number setting affects only operational quota discovery.
