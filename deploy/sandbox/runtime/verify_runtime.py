"""Verify the built Python runtime and optionally write its reproducible manifest."""

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def verify_plotting() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ['MPLCONFIGDIR'] = temporary
        import matplotlib as mpl  # noqa: PLC0415

        mpl.use('Agg')
        from matplotlib import pyplot as plt  # noqa: PLC0415

        figure, axis = plt.subplots()
        try:
            axis.plot([1, 2], [2, 4])
            output = io.BytesIO()
            figure.savefig(output, format='png')
            if not output.getvalue().startswith(b'\x89PNG\r\n\x1a\n'):
                raise RuntimeError('Matplotlib image generation failed')
        finally:
            plt.close(figure)


def locked_packages(lock: Path, declared: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in lock.read_text().splitlines():
        if not line or line[0].isspace() or line.startswith('#'):
            continue
        requirement = Requirement(line.rstrip('\\').strip())
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        version = importlib.metadata.version(requirement.name)
        if version not in requirement.specifier:
            raise RuntimeError(f'Installed dependency does not match the lock: {requirement.name}')
        packages[canonicalize_name(requirement.name)] = version

    for line in declared.read_text().splitlines():
        value = line.split('#', 1)[0].strip()
        if not value:
            continue
        requirement = Requirement(value)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        version = packages.get(canonicalize_name(requirement.name))
        if version is None or version not in requirement.specifier:
            raise RuntimeError(f'Dependency declaration requires an updated lock: {requirement.name}')
    return packages


def verify(lock: Path) -> dict[str, object]:
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('The workspace image requires Python 3.12')
    packages = locked_packages(lock, lock.with_name('requirements.in'))

    import cv2  # noqa: PLC0415 - exercise native runtime imports after verifying versions
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import pypdfium2  # noqa: PLC0415
    from docx import Document  # noqa: PLC0415
    from openpyxl import Workbook  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415
    from pypdf import PdfWriter  # noqa: PLC0415

    image = np.zeros((32, 48, 3), dtype=np.uint8)
    success, encoded = cv2.imencode('.png', image)
    if not success or Image.open(io.BytesIO(encoded.tobytes())).size != (48, 32):
        raise RuntimeError('OpenCV/Pillow image generation failed')
    expected_total = sum([2, 3])
    if int(pd.DataFrame({'amount': [2, 3]}).amount.sum()) != expected_total:
        raise RuntimeError('Pandas calculation failed')
    Workbook().save(io.BytesIO())
    Document().save(io.BytesIO())
    pdf = PdfWriter()
    pdf.add_blank_page(width=72, height=72)
    content = io.BytesIO()
    pdf.write(content)
    document = pypdfium2.PdfDocument(content.getvalue())
    try:
        if len(document) != 1:
            raise RuntimeError('PDF rendering library failed')
    finally:
        document.close()

    verify_plotting()
    office = subprocess.run(
        ['/usr/bin/soffice', '--headless', '--version'],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.strip()
    return {
        'python': platform.python_version(),
        'architecture': platform.machine(),
        'lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
        'requirements_sha256': hashlib.sha256(lock.with_name('requirements.in').read_bytes()).hexdigest(),
        'packages': packages,
        'office': office,
        'checks': ['image', 'dataframe', 'xlsx', 'docx', 'pdf', 'matplotlib'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=Path(__file__).with_name('requirements.lock'))
    parser.add_argument('--manifest', type=Path)
    arguments = parser.parse_args()
    result = verify(arguments.lock)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if arguments.manifest is not None:
        arguments.manifest.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
