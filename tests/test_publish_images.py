"""Verify actual publishing commands without building or pushing an image."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('publisher_under_test', Path(__file__).resolve().parents[1] / 'scripts/publish_images.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def publish(self, registry, layout=None):
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name in ('Dockerfile', 'backend/Dockerfile', 'frontend/Dockerfile'):
                content = b'FROM scratch\n'
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'release.json'
            argv = ['publish_images.py', '--registry', registry, '--output', str(output)]
            if layout:
                argv += ['--repository-layout', layout]
            if publisher.APPLICATION == 'abacus':
                argv += ['--babar', str(root / 'babar'), '--babar-revision', 'b' * 40]
            builds = []

            def git(args, **kwargs):
                if 'archive' in args:
                    return archive.getvalue()
                revision = 'a' * 40 if args[args.index('-C') + 1] == str(root) else 'b' * 40
                return revision + '\n'

            def build(args, **kwargs):
                self.assertEqual(args[0], 'docker')
                builds.append(args)
                metadata = Path(args[args.index('--metadata-file') + 1])
                metadata.write_text(json.dumps({'containerimage.digest': 'sha256:' + 'd' * 64}))
                return subprocess.CompletedProcess(args, 0)

            with patch.object(publisher, 'ROOT', root), patch.object(sys, 'argv', argv), \
                 patch.object(publisher.subprocess, 'check_output', side_effect=git), \
                 patch.object(publisher.subprocess, 'run', side_effect=build):
                publisher.main()
            return json.loads(output.read_text()), builds

    def test_existing_local_repository_names_and_digests_are_preserved(self):
        release, builds = self.publish('127.0.0.1:5001')
        for component, image in release['images'].items():
            self.assertEqual(image, f'127.0.0.1:5001/{publisher.APPLICATION}/{component}@sha256:' + 'd' * 64)
        self.assertEqual(len(builds), len(publisher.IMAGES))

    def test_docker_hub_commands_use_flat_names_and_commit_tags(self):
        release, builds = self.publish('docker.io/example')
        self.assertEqual(release['source']['revision'], 'a' * 40)
        for component, image in release['images'].items():
            self.assertEqual(image, f'docker.io/example/{publisher.APPLICATION}-{component}@sha256:' + 'd' * 64)
        for args in builds:
            self.assertIn('--push', args)
            self.assertTrue(args[args.index('-t') + 1].endswith(':' + 'a' * 40))
        if publisher.APPLICATION == 'abacus':
            self.assertEqual(release['babar']['revision'], 'b' * 40)

    def test_flat_layout_supports_other_registries(self):
        release, _ = self.publish('ghcr.io/example', 'flat')
        self.assertTrue(all(image.startswith(f'ghcr.io/example/{publisher.APPLICATION}-') for image in release['images'].values()))

    def test_invalid_docker_hub_prefix_and_nested_layout_are_rejected(self):
        for registry, layout in [('docker.io', None), ('docker.io/user/nested', None), ('docker.io/user', 'nested')]:
            with self.subTest(registry=registry), self.assertRaises(ValueError):
                publisher.image_repository(registry, 'api', layout)


if __name__ == '__main__':
    unittest.main()
