"""Audit the existing local ZIP against its extracted files without downloading.

This proves local package integrity, not organizer provenance or model accuracy.
"""
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
ARCHIVE = BASE / 'competition.zip'
OUTPUT = ROOT / 'work/local-package-audit/summary.json'


def digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        result.update(block)
    return result.hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return digest(stream)


assert not OUTPUT.exists(), 'Preserve the existing audit result'
archive_hash = file_digest(ARCHIVE)
records = []
with zipfile.ZipFile(ARCHIVE) as archive:
    names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
    assert len(names) == len(set(names)), 'Duplicate archive file names'
    for name in names:
        relative = PurePosixPath(name)
        assert relative.parts[0] == 'competition' and not relative.is_absolute()
        assert '..' not in relative.parts
        target = BASE / name
        assert target.is_file(), name
        with archive.open(name) as stream:
            archived = digest(stream)  # Reading to EOF also checks the ZIP CRC.
        actual = file_digest(target)
        assert actual == archived, name
        records.append({'path': name, 'bytes': target.stat().st_size,
                        'sha256': actual, 'matches_archive': True})

    manifest = archive.read('competition/SHA256SUMS').decode('utf-8')
    listed = []
    for line in manifest.splitlines():
        wanted, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        assert len(wanted) == 64
        assert 'competition/' + name in names, name
        assert file_digest(BASE / 'competition' / name) == wanted, name
        listed.append(name)
    rows = list(csv.DictReader(io.StringIO(
        archive.read('competition/reference_top1_5.csv').decode('utf-8-sig'))))
    reference = json.loads(archive.read('competition/reference_top1_5.json'))
    assert reference['class_index_base'] == 0 and reference['class_count'] == 1000
    assert len(rows) == len(reference['images']) == 5
    for row, image in zip(rows, reference['images']):
        assert row['image'] == image['file']
        for model, value in image['top1'].items():
            assert 0 <= int(row[model]) == value['index'] < 1000

assert file_digest(ARCHIVE) == archive_hash, 'Archive changed during audit'
unlisted = sorted(set(names) - {'competition/' + name for name in listed}
                  - {'competition/SHA256SUMS'})
result = {
    'status': 'LOCAL_PACKAGE_INTEGRITY_PASS_OFFICIAL_ACCURACY_UNPROVEN',
    'archive': str(ARCHIVE), 'archive_bytes': ARCHIVE.stat().st_size,
    'archive_sha256': archive_hash,
    'audit_script_sha256': file_digest(Path(__file__)),
    'files_identical_to_archive': len(records),
    'SHA256SUMS_entries_verified': len(listed),
    'public_reference_CSV_JSON_consistent': True,
    'files_not_listed_in_SHA256SUMS': unlisted,
    'file_results': records,
    'network_requests': 0,
    'scope': 'Read-only audit of the existing local ZIP, CRCs, extracted bytes, '
             'embedded checksum manifest and public-reference file consistency. '
             'Does not authenticate against a remote attachment, regenerate '
             'references, accept model accuracy, or change any supplied file.',
}
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: result[key] for key in [
    'status', 'files_identical_to_archive', 'SHA256SUMS_entries_verified',
    'files_not_listed_in_SHA256SUMS', 'network_requests']}, ensure_ascii=False))
