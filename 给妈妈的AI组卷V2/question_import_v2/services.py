"""Private evidence services. Production services are supplied by session 4."""
import hashlib
import mimetypes
from pathlib import Path

class EvidenceServices:
    """Asset identities are real; unavailable compilation is explicitly failed."""
    def compile_formula(self, latex, source_ref=None):
        return {'ok': False, 'latex': latex, 'mathml': None, 'omml': None,
                'compiler_version': 'unavailable-evidence-service',
                'diagnostics': ['Production compiler not injected; source recovery only.']}

    def register_asset(self, local_path, preview_path=None):
        path = Path(local_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {'id': 'asset-' + digest, 'sha256': digest,
                'mime_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                'preview_url': '/api/v2/content/assets/asset-' + digest}
