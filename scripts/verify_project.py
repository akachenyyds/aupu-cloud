#!/usr/bin/env python3
"""Bounded public-tree, privacy, metadata and syntax checks; no network."""
import argparse
import ast
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'dist'}
ALLOWED_SUFFIXES = {'.py', '.json', '.md', '.yaml', '.yml', '.txt'}
ALLOWED_DOTFILES = {'.gitignore', '.gitattributes'}


def public_files():
    return sorted(p for p in ROOT.rglob('*') if p.is_file()
                  and not any(part in EXCLUDED for part in p.relative_to(ROOT).parts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    issues = []
    files = public_files()

    def issue(path, rule, line=None):
        # Values are deliberately never echoed by this scanner.
        issues.append({'path': str(path.relative_to(ROOT)), 'rule': rule, 'line': line})

    private_examples = {'10.23.45.67', '10.23.45.68', '10.0.0.0',
                        '172.16.0.0', '192.168.0.0', '127.0.0.1'}
    mac_examples = {'02:00:00:00:00:01', '02:00:00:00:00:02',
                    '02:00:00:00:00:0a', '00:00:00:00:00:00',
                    'ff:ff:ff:ff:ff:ff', '01:00:00:00:00:01'}
    patterns = {
        'private_key': r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----',
        'jwt_literal': r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
        'github_token': r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
        'aws_access_key': r'AKIA[A-Z0-9]{16}',
        'personal_absolute_path': r'/(?:Users|home)/[A-Za-z0-9_.-]+/',
        'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    }
    for path in files:
        if path.is_symlink():
            issue(path, 'symlink')
            continue
        relative_parts = path.relative_to(ROOT).parts
        if any(part.lower() in ('private', '.storage', 'credentials', 'captures')
               or part.lower().startswith(('.env', 'secrets.', 'credentials.'))
               for part in relative_parts):
            issue(path, 'private_file_path')
        if path.name != 'LICENSE' and path.name not in ALLOWED_DOTFILES and path.suffix not in ALLOWED_SUFFIXES:
            issue(path, 'unexpected_file_type')
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            issue(path, 'not_readable_utf8')
            continue
        if path.suffix == '.py':
            try:
                ast.parse(text, filename=str(path.relative_to(ROOT)))
            except SyntaxError as exc:
                issue(path, 'python_syntax', exc.lineno)
        if path.suffix == '.json':
            try:
                json.loads(text)
            except ValueError:
                issue(path, 'json_syntax')
        for rule, pattern in patterns.items():
            for match in re.finditer(pattern, text):
                issue(path, rule, text.count('\n', 0, match.start()) + 1)
        for match in re.finditer(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', text):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # is_private also includes reserved 0/8, which can be a package
            # version such as a.b.c.d. Here we specifically inspect LAN ranges.
            lan_ranges = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
            if any(address in ipaddress.ip_network(net) for net in lan_ranges) and str(address) not in private_examples:
                issue(path, 'non_example_private_ip', text.count('\n', 0, match.start()) + 1)
        for match in re.finditer(r'(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', text):
            if match.group().lower() not in mac_examples:
                issue(path, 'non_example_mac', text.count('\n', 0, match.start()) + 1)
        if path.suffix == '.md':
            for match in re.finditer(r'\]\(([^)]+)\)', text):
                target = match.group(1).split('#')[0]
                if not target or urlsplit(target).scheme:
                    continue
                linked = (path.parent / unquote(target)).resolve()
                if not linked.is_relative_to(ROOT) or not linked.exists():
                    issue(path, 'broken_local_markdown_link', text.count('\n', 0, match.start()) + 1)

    manifest_path = ROOT / 'custom_components/aupu_cloud/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    required = ('domain', 'name', 'version', 'documentation', 'issue_tracker',
                'codeowners', 'config_flow', 'requirements', 'iot_class', 'integration_type')
    for key in required:
        if key not in manifest:
            issue(manifest_path, 'missing_manifest_' + key)
    requirements = [line.strip() for line in (ROOT / 'requirements.txt').read_text().splitlines()
                    if line.strip() and not line.startswith('#')]
    if manifest['requirements'] != requirements:
        issue(manifest_path, 'dependency_manifest_mismatch')
    if manifest.get('domain') != 'aupu_cloud' or manifest.get('codeowners') != ['@akachenyyds']:
        issue(manifest_path, 'unexpected_manifest_identity')
    for name in ('README.md', 'LICENSE', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md',
                 'CONTRIBUTING.md', 'docs/ORIGIN.md', 'docs/REVIEW.md', 'hacs.json'):
        if not (ROOT / name).is_file():
            issue(ROOT / name, 'missing_required_file')
    # Translation keys must remain synchronized for this small integration.
    def keys(value, prefix=''):
        result = set()
        if isinstance(value, dict):
            for key, item in value.items():
                result.add(prefix + key)
                result.update(keys(item, prefix + key + '.'))
        return result
    strings = json.loads((manifest_path.parent / 'strings.json').read_text())
    for path in (manifest_path.parent / 'translations').glob('*.json'):
        if keys(json.loads(path.read_text())) != keys(strings):
            issue(path, 'translation_keys_mismatch')
    report = {'files_checked': len(files), 'passed': not issues, 'issues': issues,
              'scope': 'bounded static heuristics; no claim to detect all possible secrets'}
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
