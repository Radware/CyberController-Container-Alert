# Historical Container Probe Map — example only

**Engineering reference, not an installation configuration or a current product guarantee.**
This is a snapshot from one earlier CyberController environment. Image HEALTHCHECK
definitions, service ports and probe discovery can differ by product version and
deployment. Never copy these entries into monitoring overrides without confirming
the current Docker state and obtaining the normal authorization.


Probe selection is automatic: containers with a Docker `HEALTHCHECK` are monitored passively via events; all others get an active probe auto-discovered on the first poll and cached for the container's lifetime.

| Container | Probe Type | Endpoint | Why |
|-----------|-----------|----------|-----|
| `config_kvision-infra-fluentd_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-dp-inline-config_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-collector_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-configuration-service_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-anomaly-detection-engine_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-persist-service_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-rt-alert_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_policy-editor_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-alerts_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-reporter_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-vrm_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-redis_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-health_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-webui_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-tor-feed_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-efk_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-polling-mgr_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-formatter_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-data-polling-scheduler_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-infra-cadvisor_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-scheduler_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-vdirect_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-help_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-ted_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_postgres_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-snmp-trap-collector_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-auto-engine_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-lls_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-assist-service_1` | HTTP | `:3005` (path auto-discovered) | No Docker HEALTHCHECK; Node.js service; path cached on first successful response |
| `config_kvision-infra-rabbitmq_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `config_kvision-ha-operator_1` | HTTP | `:8080` (path auto-discovered) | No Docker HEALTHCHECK; Java HTTP service; path cached on first successful response |
| `config_kvision-dc-nginx_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |
| `process-exporter` | HTTP | `:9256/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `prometheus` | HTTP | `:9090/-/ready` | No Docker HEALTHCHECK; standard Prometheus readiness endpoint |
| `alertmanager` | HTTP | `:9093/-/healthy` | No Docker HEALTHCHECK; standard Alertmanager health endpoint |
| `postgres-exporter` | HTTP | `:9187/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `mysql-exporter` | HTTP | `:9104/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `es-exporter` | HTTP | `:9114/metrics` | No Docker HEALTHCHECK; port 7979 HTTP fails; port 9114 serves `/metrics` |
| `node-exporter` | HTTP | `:9100/metrics` | No Docker HEALTHCHECK; Prometheus exporter — `/metrics` returns 200 |
| `grafana` | HTTP | `:3000/api/health` | No Docker HEALTHCHECK; Grafana health endpoint returns 200 |
| `config_kvision-infra-mariadb_1` | Docker HEALTHCHECK | Docker managed | Image has HEALTHCHECK directive |

> **Verify what was actually cached after startup:**
> ```bash
> grep -E "auto-discover|falling back" watchdog/watchdog.log
> ```
