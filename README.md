# ansible-template
Ansible repository template

## Memory balancing

The `descheduler` role installs the official Descheduler chart 0.32.2 for the
inventory's K3s 1.32 clusters. It uses Metrics Server node memory usage divided by
allocatable memory, rather than pod resource requests. Prometheus is not required.
It runs every five minutes with a band of ten percentage points above and below
the unweighted average of node percentages. A cycle needs both an underutilized
and an overutilized node before it attempts eviction.

The controller includes eligible managers and workers, respects scheduling
constraints and PodDisruptionBudgets, and permits at most one eviction per node,
one per namespace, and two per cycle. Pods younger than ten minutes, single
replicas, StatefulSets, PVC/local-storage pods, DaemonSets, unmanaged pods, and
system-critical pods are protected. Infrastructure namespaces are excluded.
Do not add `descheduler.alpha.kubernetes.io/evict` to protected pods: this upstream
annotation overrides DefaultEvictor protections.

Balancing is best effort. The normal scheduler places replacement pods and may
return them to their original nodes. Protected workloads, uneven workload sizes,
host processes, and scheduling constraints can prevent the percentages from
converging. CPU and pod-count thresholds do not drive balancing, though resource
capacity still constrains placement. This role does not change workload requests,
replicas, affinity, or disruption budgets.

Deploy to the test cluster using the usual inventory and Ansible Vault credentials:

```sh
ansible-playbook -i inventory/hosts.yml deploy-middleware.yml \
  --limit test-k3s --tags descheduler --ask-vault-pass
```

One manager from each selected cluster runs the role, including when multiple
clusters are selected. A limit must include at least one manager. The middleware
workflow also exposes the `descheduler` tag. Installation requires a healthy
`v1beta1.metrics.k8s.io` API and nonempty node metrics; it fails before Helm
installation if these checks fail. K3s normally provides Metrics Server.

Override the variables in `roles/descheduler/defaults/main.yml` from inventory or
extra vars. `descheduler_memory_deviation` sets the symmetric band;
`descheduler_interval`, `descheduler_min_pod_age`, the three
`descheduler_max_evictions_*` settings, `descheduler_excluded_namespaces`, and
`descheduler_resources` control frequency and disruption. Keep the controller
namespace excluded. The chart version is pinned in `group_vars/k3s.yml`; review
release compatibility before changing it.

To inspect proposed evictions, add `-e '{"descheduler_dry_run": true}'` to the
deployment command. This installs a running controller that logs decisions but
does not evict pods; it differs from Ansible `--check`. Redeploy with
`-e '{"descheduler_dry_run": false}'` to enable automatic eviction again.

On a manager, inspect the deployment:

```sh
kubectl -n kube-system rollout status deployment/descheduler
kubectl top nodes
kubectl -n kube-system logs deployment/descheduler --since=15m
kubectl get pods -A -o wide
```

For test-cluster acceptance, inspect several cycles with balanced nodes and a
deliberately uneven replicated stateless workload. Verify the eviction caps,
replacement readiness, and declining imbalance where scheduling permits. Repeat
with a blocking PDB, a single replica, a StatefulSet, PVC/local storage, and a pod
pinned to one node; these must remain protected. Verify dry-run leaves pod UIDs
unchanged and unavailable metrics cause errors without request-based fallback.
Repeat deployment and confirm no unnecessary rollout. These runtime checks
require cluster access and are separate from local rendering tests.

Run local checks with Ansible, PyYAML, Helm 3, and the pinned chart archive:

```sh
ansible-playbook -i inventory/hosts.yml deploy-middleware.yml --syntax-check
helm repo add descheduler https://kubernetes-sigs.github.io/descheduler/
helm pull descheduler/descheduler --version 0.32.2 --destination /tmp
DESCHEDULER_CHART=/tmp/descheduler-0.32.2.tgz python3 -m unittest discover -s tests
```
