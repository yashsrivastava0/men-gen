"""Frozen v1 contracts: Pydantic models and Protocols only. No I/O.

Import from the submodules (``mde_contracts.slots``, ``.cards``, ``.events``, ``.brief``,
``.research``, ``.spec``, ``.plan``, ``.policy``, ``.content``, ``.gateway``, ``.calls``,
``.engines``, ``.records``, ``.ports``, ``.registry``).
"""

from mde_contracts.common import CONTRACTS_VERSION, SPEC_SCHEMA_ID

__version__ = "0.1.0"

__all__ = ["CONTRACTS_VERSION", "SPEC_SCHEMA_ID", "__version__"]
