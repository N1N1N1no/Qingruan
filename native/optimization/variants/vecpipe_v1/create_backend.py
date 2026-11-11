"""Replace only the vector instance in an isolated shared backend."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
source = ROOT / 'rtl/axi_accel_shared_backend.sv'
target = HERE / 'axi_accel_backend_vecpipe.sv'
assert not target.exists(), 'Preserve generated source'
text = source.read_text()
old = 'module axi_accel_backend #('
assert text.count(old) == 1
text = text.replace(old, 'module axi_accel_backend_vecpipe #(')
old = 'axi_vector_engine #('
assert text.count(old) == 1
text = text.replace(old, 'axi_vector_engine_vecpipe #(')
target.write_text(text)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


(HERE / 'backend-generation.json').write_text(json.dumps({
    'status': 'VECPIPE_SHARED_BACKEND_SOURCE_GENERATED_NOT_VERIFIED',
    'original_source': str(source), 'original_sha256': sha(source),
    'generated_source': str(target), 'generated_sha256': sha(target),
    'generator_sha256': sha(Path(__file__)),
    'scope': 'Only top module name and vector engine type changed. Existing shared FP32 DIV path, ownership, all other engines and single physical array preserved.'
}, indent=2) + '\n')
print('VECPIPE_SHARED_BACKEND_SOURCE_GENERATED_NOT_VERIFIED')
