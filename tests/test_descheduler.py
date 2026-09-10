"""Render the actual role through Ansible and the pinned Helm chart, without a cluster."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


ROOT = Path(__file__).resolve().parents[1]


class DeschedulerRendering(unittest.TestCase):
    def render(self, **overrides):
        variables = yaml.safe_load((ROOT / 'roles/descheduler/defaults/main.yml').read_text())
        variables.update(overrides)
        values = Templar(loader=DataLoader(), variables=variables).template(
            (ROOT / 'roles/descheduler/templates/values.yml.j2').read_text()
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'values.yaml'
            path.write_text(values)
            result = subprocess.run(
                [os.environ.get('HELM', 'helm'), 'template', 'descheduler',
                 os.environ['DESCHEDULER_CHART'], '--namespace', 'kube-system',
                 '--values', str(path)],
                check=True, capture_output=True, text=True,
            )
        return {doc['kind']: doc for doc in yaml.safe_load_all(result.stdout) if doc}

    def test_live_policy_and_permissions(self):
        docs = self.render()
        self.assertNotIn('CronJob', docs)
        pod = docs['Deployment']['spec']['template']['spec']
        self.assertEqual(docs['Deployment']['spec']['replicas'], 1)
        container = pod['containers'][0]
        version = yaml.safe_load((ROOT / 'group_vars/k3s.yml').read_text())['descheduler_helm_chart_version']
        self.assertEqual(container['image'], f'registry.k8s.io/descheduler/descheduler:v{version}')
        self.assertIn('--leader-elect=true', container['args'])
        self.assertIn('--dry-run=false', container['args'])
        policy = yaml.safe_load(docs['ConfigMap']['data']['policy.yaml'])
        self.assertTrue(policy['metricsCollector']['enabled'])
        self.assertNotIn('gracePeriodSeconds', policy)
        self.assertNotIn('nodeSelector', policy)
        self.assertEqual([policy[key] for key in (
            'maxNoOfPodsToEvictPerNode', 'maxNoOfPodsToEvictPerNamespace',
            'maxNoOfPodsToEvictTotal')], [1, 1, 2])
        profile, = policy['profiles']
        self.assertEqual(profile['plugins']['balance']['enabled'], ['LowNodeUtilization'])
        self.assertNotIn('deschedule', profile['plugins'])
        plugins = {item['name']: item['args'] for item in profile['pluginConfig']}
        balance = plugins['LowNodeUtilization']
        self.assertTrue(balance['metricsUtilization']['metricsServer'])
        self.assertTrue(balance['useDeviationThresholds'])
        self.assertEqual(balance['thresholds'], {'cpu': 0, 'pods': 0, 'memory': 10})
        self.assertEqual(balance['thresholds'], balance['targetThresholds'])
        evictor = plugins['DefaultEvictor']
        self.assertTrue(evictor['nodeFit'])
        self.assertTrue(evictor['ignorePvcPods'])
        self.assertEqual(evictor['minReplicas'], 2)
        self.assertEqual(evictor['minPodAge'], '10m')
        for key in ('evictLocalStoragePods', 'evictDaemonSetPods',
                    'evictSystemCriticalPods', 'evictFailedBarePods'):
            self.assertFalse(evictor[key])
        self.assertIn({'key': 'statefulset.kubernetes.io/pod-name', 'operator': 'DoesNotExist'},
                      evictor['labelSelector']['matchExpressions'])
        self.assertTrue({'kube-system', 'longhorn-system', 'monitoring', 'traefik-system'}
                        <= set(balance['evictableNamespaces']['exclude']))
        rules = docs['ClusterRole']['rules']
        self.assertTrue(any('metrics.k8s.io' in r['apiGroups']
                            and {'nodes', 'pods'} <= set(r['resources']) for r in rules))
        self.assertTrue(any('pods/eviction' in r['resources'] and 'create' in r['verbs'] for r in rules))

    def test_dry_run_and_overrides(self):
        docs = self.render(descheduler_dry_run=True, descheduler_interval='15m',
                           descheduler_memory_deviation=5)
        args = docs['Deployment']['spec']['template']['spec']['containers'][0]['args']
        self.assertIn('--dry-run=true', args)
        self.assertNotIn('--leader-elect=true', args)
        self.assertIn('--descheduling-interval=15m', args)
        policy = yaml.safe_load(docs['ConfigMap']['data']['policy.yaml'])
        balance = policy['profiles'][0]['pluginConfig'][1]['args']
        self.assertEqual(balance['thresholds']['memory'], 5)
        self.assertEqual(balance['targetThresholds']['memory'], 5)

    def test_one_manager_per_selected_cluster(self):
        play = next(p for p in yaml.safe_load((ROOT / 'deploy-middleware.yml').read_text())
                    if p['name'] == 'Install Descheduler')
        self.assertNotIn('run_once', play)
        expression = '{{ ' + play['tasks'][0]['when'] + ' }}'
        groups = {'test-k3s-managers': ['test1', 'test2'],
                  'game-k3s-managers': ['game1', 'game2']}
        for selected, expected in [(['test1', 'test2', 'game1', 'game2'], ['test1', 'game1']),
                                   (['test2', 'game2'], ['test2', 'game2']),
                                   (['test2'], ['test2'])]:
            chosen = []
            for host in selected:
                variables = dict(groups=groups, inventory_hostname=host,
                                 cluster_name='test' if host.startswith('test') else 'game',
                                 ansible_play_hosts_all=selected)
                if Templar(loader=DataLoader(), variables=variables).template(expression):
                    chosen.append(host)
            self.assertEqual(chosen, expected)


if __name__ == '__main__':
    unittest.main()
