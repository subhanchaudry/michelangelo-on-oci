"""Triton Python backend adapter. The same scorer is called by batch tasks."""
import json
from pathlib import Path
import numpy as np
import triton_python_backend_utils as pb_utils
from scoring import score, load_model


class TritonPythonModel:
    def initialize(self, args):
        version = Path(args['model_repository']) / args['model_version']
        self.metadata = json.loads((version / 'metadata.json').read_text())
        if self.metadata.get('contract_version') != 1:
            raise ValueError('Incompatible model contract')
        self.demo = self.metadata['demo']
        self.model = load_model(self.demo, version)

    def execute(self, requests):
        responses = []
        for request in requests:
            try:
                tensor = pb_utils.get_input_tensor_by_name(request, 'RECORDS')
                if tensor is None or tensor.as_numpy().size != 1:
                    raise ValueError('RECORDS must contain one JSON record array')
                records = json.loads(tensor.as_numpy().reshape(-1)[0])
                scores = np.asarray(score(self.demo, self.model, records), dtype=np.float64)
                responses.append(pb_utils.InferenceResponse(output_tensors=[pb_utils.Tensor('SCORES', scores)]))
            except Exception as exc:
                code = pb_utils.TritonError.INVALID_ARG if isinstance(exc, (ValueError, TypeError, KeyError)) else pb_utils.TritonError.INTERNAL
                responses.append(pb_utils.InferenceResponse(error=pb_utils.TritonError(str(exc), code)))
        return responses
