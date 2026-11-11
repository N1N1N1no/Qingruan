"""Official preprocessing and immutable ONNX baseline against public labels."""
import csv
import importlib.util
import time
import numpy as np
import onnxruntime as ort

from .model import MODEL_FILE, sha


def session(path):
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])


def preprocess(competition, name):
    spec = importlib.util.spec_from_file_location('swin_official_preprocess', competition/'official_preprocess.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_classification_input('swin_tiny_patch4_window7_224', competition/'images'/name, False)


def run(competition, output):
    path = competition/'models'/MODEL_FILE
    before = sha(path)
    runtime = session(path)
    items = []
    with (competition/'reference_top1_5.csv').open(encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            x = preprocess(competition,row['image'])
            started=time.monotonic()
            y=runtime.run(['output'],{'input':x})[0]
            pred=int(np.argmax(y[0]))
            expected=int(row['Swin-Tiny'])
            np.save(output/(row['image']+'.input.npy'),x)
            np.save(output/(row['image']+'.logits.npy'),y)
            items.append({'image':row['image'],'actual_top1':pred,'expected_top1':expected,
                          'pass':pred==expected,'elapsed_s':time.monotonic()-started,
                          'input_sha256':sha(output/(row['image']+'.input.npy'))})
    assert sha(path)==before
    return {'status':'PASS' if all(i['pass'] for i in items) else 'FAIL',
            'scope':'CPU ONNX baseline only; not RISC-V/PE inference',
            'onnxruntime':ort.__version__,'providers':runtime.get_providers(),
            'model_sha256':before,'preprocess_sha256':sha(competition/'official_preprocess.py'),
            'graph_optimization':'disabled','results':items}
