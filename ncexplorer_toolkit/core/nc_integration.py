import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Callable, Union, Tuple

from ..utils.tempfile_store import TempFileStore

# Use proper path type hints
PathLike = Union[str, os.PathLike]


@dataclass(slots=True)
class NCExplorerResult:
    """Structured result of a cdo operation."""
    success: bool
    stdout: str
    stderr: str
    output_file: Optional[str] = None
    execution_time: float = 0.0

    def __bool__(self) -> bool:  # allow `if result:` semantics
        return self.success


class NCExplorerError(Exception):
    """Custom exception raised for cdo-related failures."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "", returncode: Optional[int] = None):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.msg = f'(returncode:{returncode}) {stderr}'

    def __str__(self):
        return self.msg


class NCExplorerIntegration:
    """
    Cross-platform integration layer for the cdo command-line tool.  On Windows,
    the wrapper automatically chooses between a native build and the WSL binary.
    """

    # ----- Complete operator list from cdo reference card -----
    ALL_OPERATORS = [
        # Information
        'info', 'infov', 'map', 'sinfo', 'sinfov', 'diff', 'diffv', 'npar',
        'nlevel', 'nyear', 'nmon', 'ndate', 'ntime', 'showformat', 'showcode',
        'showname', 'showstdname', 'showlevel', 'showltype', 'showyear',
        'showmon', 'showdate', 'showtime', 'pardes', 'griddes', 'vct',

        # File operations
        'copy', 'cat', 'replace', 'merge', 'mergetime', 'splitcode',
        'splitname', 'splitlevel', 'splitgrid', 'splitzaxis', 'splithour',
        'splitday', 'splitmon', 'splitseas', 'splityear', 'splitsel',

        # Selection
        'selcode', 'delcode', 'selname', 'delname', 'selstdname', 'sellevel',
        'selgrid', 'selgridname', 'selzaxis', 'selzaxisname', 'selltype',
        'seltabnum', 'seltimestep', 'seltime', 'selhour', 'selday', 'selmon',
        'selyear', 'selseas', 'seldate', 'selsmon', 'sellonlatbox', 'selindexbox',

        # Conditional selection
        'ifthen', 'ifnotthen', 'ifthenelse', 'ifthenc', 'ifnotthenc',

        # Comparison
        'eq', 'ne', 'le', 'lt', 'ge', 'gt', 'eqc', 'nec', 'lec', 'ltc', 'gec', 'gtc',

        # Modification
        'setpartab', 'setcode', 'setname', 'setlevel', 'setltype', 'setdate',
        'settime', 'setday', 'setmon', 'setyear', 'settunits', 'settaxis',
        'setreftime', 'setcalendar', 'shifttime', 'chcode', 'chname',
        'chlevel', 'chlevelc', 'chlevelv', 'setgrid', 'setgridtype',
        'setzaxis', 'setgatt', 'setgatts', 'invertlat', 'invertlon',
        'invertlatdes', 'invertlondes', 'invertlatdata', 'invertlondata',
        'maskregion', 'masklonlatbox', 'maskindexbox', 'setclonlatbox',
        'setcindexbox', 'enlarge', 'setmissval', 'setctomiss', 'setmisstoc',
        'setrtomiss',

        # Arithmetic
        'expr', 'exprf', 'abs', 'int', 'nint', 'sqr', 'sqrt', 'exp', 'ln',
        'log10', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'addc', 'subc',
        'mulc', 'divc', 'add', 'sub', 'mul', 'div', 'min', 'max', 'atan2',
        'ymonadd', 'ymonsub', 'ymonmul', 'ymondiv', 'muldpm', 'divdpm',
        'muldpy', 'divdpy',

        # Statistical values
        'ensmin', 'ensmax', 'enssum', 'ensmean', 'ensavg', 'ensvar', 'ensstd',
        'enspctl', 'fldmin', 'fldmax', 'fldsum', 'fldmean', 'fldavg',
        'fldvar', 'fldstd', 'fldpctl', 'zonmin', 'zonmax', 'zonsum',
        'zonmean', 'zonavg', 'zonvar', 'zonstd', 'zonpctl', 'mermin',
        'mermax', 'mersum', 'mermean', 'meravg', 'mervar', 'merstd',
        'merpctl', 'vertmin', 'vertmax', 'vertsum', 'vertmean', 'vertavg',
        'vertvar', 'vertstd', 'timselmin', 'timselmax', 'timselsum',
        'timselmean', 'timselavg', 'timselvar', 'timselstd', 'timselpctl',
        'runmin', 'runmax', 'runsum', 'runmean', 'runavg', 'runvar',
        'runstd', 'runpctl', 'timmin', 'timmax', 'timsum', 'timmean',
        'timavg', 'timvar', 'timstd', 'timpctl', 'hourmin', 'hourmax',
        'hoursum', 'hourmean', 'houravg', 'hourvar', 'hourstd', 'hourpctl',
        'daymin', 'daymax', 'daysum', 'daymean', 'dayavg', 'dayvar',
        'daystd', 'daypctl', 'monmin', 'monmax', 'monsum', 'monmean',
        'monavg', 'monvar', 'monstd', 'monpctl', 'yearmin', 'yearmax',
        'yearsum', 'yearmean', 'yearavg', 'yearvar', 'yearstd', 'yearpctl',
        'seasmin', 'seasmax', 'seassum', 'seasmean', 'seasavg', 'seasvar',
        'seasstd', 'seaspctl', 'ydaymin', 'ydaymax', 'ydaysum', 'ydaymean',
        'ydayavg', 'ydayvar', 'ydaystd', 'ydaypctl', 'ymonmin', 'ymonmax',
        'ymonsum', 'ymonmean', 'ymonavg', 'ymonvar', 'ymonstd', 'ymonpctl',
        'yseasmin', 'yseasmax', 'yseassum', 'yseasmean', 'yseasavg',
        'yseasvar', 'yseasstd', 'yseaspctl', 'ydrunmin', 'ydrunmax',
        'ydrunsum', 'ydrunmean', 'ydrunavg', 'ydrunvar', 'ydrunstd',
        'ydrunpctl',

        # Regression
        'detrend', 'trend', 'subtrend',

        # Interpolation
        'remapbil', 'remapbic', 'remapcon', 'remapdis', 'genbil', 'genbic',
        'gencon', 'gendis', 'remap', 'interpolate', 'intgridbil', 'remapeta',
        'ml2pl', 'ml2hl', 'inttime', 'intntime', 'intyear',

        # Transformation
        'sp2gp', 'sp2gpl', 'gp2sp', 'gp2spl', 'sp2sp', 'spcut', 'dv2uv',
        'dv2uvl', 'uv2dv', 'uv2dvl',

        # Formatted I/O
        'input', 'inputsrv', 'inputext', 'output', 'outputf', 'outputint',
        'outputsrv', 'outputext',

        # Miscellaneous
        'gradsdes1', 'gradsdes2', 'smooth9', 'setrtoc', 'setrtoc2', 'timsort',
        'const', 'random', 'rotuvb', 'mastrfu', 'histcount', 'histsum',
        'histmean', 'histfreq', 'wct', 'fdns', 'strwin', 'strbre', 'strgal',
        'hurr',

        # ECA indices
        'eca_cdd', 'eca_cfd', 'eca_csu', 'eca_cwd', 'eca_cwdi', 'eca_cwfi',
        'eca_etr', 'eca_fd', 'eca_gsl', 'eca_hd', 'eca_hwdi', 'eca_hwfi',
        'eca_id', 'eca_r10mm', 'eca_r20mm', 'eca_r75p', 'eca_r75ptot',
        'eca_r90p', 'eca_r90ptot', 'eca_r95p', 'eca_r95ptot', 'eca_r99p',
        'eca_r99ptot', 'eca_rr1', 'eca_rx1day', 'eca_rx5day', 'eca_sdii',
        'eca_su', 'eca_tg10p', 'eca_tg90p', 'eca_tn10p', 'eca_tn90p',
        'eca_tr', 'eca_tx10p', 'eca_tx90p'
    ]

    # ----- Category sets -----
    INFO_OPERATORS = {
        "info", "infov", "map", "sinfo", "sinfov", "npar", "nlevel",
        "nyear", "nmon", "ndate", "ntime", "showformat", "showcode",
        "showname", "showstdname", "showlevel", "showltype", "showyear",
        "showmon", "showdate", "showtime", "pardes", "griddes", "vct"
    }

    SINGLE_FILE_OPERATORS = {
        "detrend", "setmissval", "setctomiss", "setmisstoc", "setrtomiss",
        "abs", "int", "nint", "sqr", "sqrt", "exp", "ln", "log10", "sin",
        "cos", "tan", "asin", "acos", "atan", "addc", "subc", "mulc", "divc"
    }

    TWO_INPUT_OPERATORS = {
        "add", "sub", "mul", "div", "min", "max", "atan2",
        "eq", "ne", "le", "lt", "ge", "gt",
        "diff", "diffv",
        "ymonadd", "ymonsub", "ymonmul", "ymondiv",
        "ifthen", "ifnotthen",
        "wct", "fdns",
        "eca_cwdi", "eca_cwfi", "eca_hwdi", "eca_hwfi",
        "eca_r75p", "eca_r75ptot", "eca_r90p", "eca_r90ptot",
        "eca_r95p", "eca_r95ptot", "eca_r99p", "eca_r99ptot",
        "eca_tg10p", "eca_tg90p", "eca_tn10p", "eca_tn90p",
        "eca_tx10p", "eca_tx90p",
        "subtrend", "replace", "eca_etr"
    }

    THREE_INPUT_OPERATORS = {
        "ifthenelse",
        "timpctl", "hourpctl", "daypctl", "monpctl", "yearpctl", "seaspctl",
        "ydaypctl", "ymonpctl", "yseaspctl", "ydrunpctl"
    }

    MULTI_INPUT_OPERATORS = {
        "cat", "merge", "mergetime",
        "ensmin", "ensmax", "enssum", "ensmean", "ensavg",
        "ensvar", "ensstd", "enspctl",
        "output", "outputf", "outputint", "outputsrv", "outputext"
    }

    SELECTION_OPERATORS = {
        "selcode", "delcode", "selname", "delname", "selstdname",
        "sellevel", "selgrid", "selgridname", "selzaxis",
        "selzaxisname", "selltype", "seltabnum", "seltimestep",
        "seltime", "selhour", "selday", "selmon", "selyear",
        "selseas", "seldate", "selindexbox", "sellonlatbox"
    }

    RUNNING_OPERATORS = {
        "runmin", "runmax", "runsum", "runmean", "runavg",
        "runvar", "runstd", "runpctl", "ydrunmin", "ydrunmax",
        "ydrunsum", "ydrunmean", "ydrunavg", "ydrunvar",
        "ydrunstd", "ydrunpctl"
    }

    EXTRA_PARAM_COUNTS = {
        # selection
        "selcode": 1, "delcode": 1, "selname": 1, "delname": 1,
        "selstdname": 1, "sellevel": 1, "selgrid": 1, "selgridname": 1,
        "selzaxis": 1, "selzaxisname": 1, "selltype": 1, "seltabnum": 1,
        "seltimestep": 1, "seltime": 1, "selhour": 1, "selday": 1,
        "selmon": 1, "selyear": 1, "selseas": 1,
        "seldate": 2, "selsmon": 3,
        "sellonlatbox": 4, "selindexbox": 4,
        # modification
        "setpartab": 1, "setcode": 1, "setname": 1, "setlevel": 1,
        "setltype": 1, "setdate": 1, "settime": 1, "setday": 1,
        "setmon": 1, "setyear": 1, "settunits": 1,
        "settaxis": 3, "setreftime": 2, "setcalendar": 1, "shifttime": 1,
        "chcode": 1, "chname": 1, "chlevel": 1,
        "chlevelc": 3, "chlevelv": 3,
        "setgrid": 1, "setgridtype": 1, "setzaxis": 1,
        "setgatt": 2, "setgatts": 1,
        "maskregion": 1, "masklonlatbox": 4, "maskindexbox": 4,
        "setclonlatbox": 5, "setcindexbox": 5,
        "setmissval": 1, "setctomiss": 1, "setmisstoc": 1,
        "setrtomiss": 2, "enlarge": 1,
        # arithmetic
        "expr": 1, "exprf": 1,
        "addc": 1, "subc": 1, "mulc": 1, "divc": 1,
        # statistical / percentile
        "timpctl": 1, "monpctl": 1, "yearpctl": 1, "seaspctl": 1,
        "ydaypctl": 1, "ymonpctl": 1, "yseaspctl": 1,
        "ydrunpctl": 2,
        "timselpctl": 4,
        "runpctl": 2,
        "timselmin": 3, "timselmax": 3, "timselsum": 3,
        "timselmean": 3, "timselavg": 3, "timselvar": 3, "timselstd": 3,
        "runmin": 1, "runmax": 1, "runsum": 1, "runmean": 1,
        "runavg": 1, "runvar": 1, "runstd": 1,
        "ydrunmin": 1, "ydrunmax": 1, "ydrunsum": 1,
        "ydrunmean": 1, "ydrunavg": 1, "ydrunvar": 1, "ydrunstd": 1,
        # interpolation
        "remapbil": 1, "remapbic": 1, "remapcon": 1, "remapdis": 1,
        "genbil": 1, "genbic": 1, "gencon": 1, "gendis": 1,
        "remap": 2, "interpolate": 1, "intgridbil": 1,
        "remapeta": 2, "ml2pl": 1, "ml2hl": 1,
        "inttime": 3, "intntime": 1, "intyear": 1,
        # formatted I/O
        "input": 1, "outputf": 2,
        # misc
        "setrtoc": 3, "setrtoc2": 4,
        "const": 2, "random": 1, "rotuvb": 1, "strwin": 1,
        # ECA indices
        "eca_csu": 1, "eca_cwdi": 2, "eca_cwfi": 1, "eca_gsl": 2,
        "eca_hd": 2, "eca_hwdi": 2, "eca_hwfi": 1,
        "eca_rx1day": 1, "eca_su": 1,
    }

    # ----- Construction -----

    def __init__(self,
                 *,
                 NCExplorer_binary_path: str = "cdo",
                 temp_dir: Optional[str] = None,
                 use_wsl: Optional[bool] = None,
                 force_platform: Optional[str] = None,
                 auto_find_NCExplorer: bool = True,
                 ) -> None:

        self.NCExplorer_binary = NCExplorer_binary_path
        self.logger = logging.getLogger(self.__class__.__name__)
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self._tstore = TempFileStore(temp_dir)
        self.last_command: str = ""

        # Detect OS
        self.platform = (force_platform or platform.system()).lower()

        # Try to auto-find NCExplorer if the default path doesn't work
        if auto_find_NCExplorer and not self._test_NCExplorer_availability(use_wsl=False):
            found_NCExplorer = self._find_NCExplorer_binary()
            if found_NCExplorer:
                self.NCExplorer_binary = found_NCExplorer
                self.logger.info(f"Found NCExplorer at: {found_NCExplorer}")

        if self.platform == "windows":
            self.use_wsl = self._init_windows_NCExplorer(use_wsl)
        elif self.platform in ("linux", "darwin"):
            self.use_wsl = False
            if not self._verify_unix_NCExplorer():
                # Provide helpful error message with suggestions
                error_msg = self._get_installation_help()
                raise NCExplorerError(error_msg)
        else:
            raise NCExplorerError(f"Unsupported platform: {self.platform}")

        self.operator_signatures, self.operator_descriptions = self._load_operator_metadata()

        # Dynamically create operator methods
        self._generate_operator_methods()

    def _load_operator_metadata(self) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, str]]:
        """Load operator signatures from the installed CDO binary, with static fallback."""
        command = self._build_command([self.NCExplorer_binary, "--operators"])
        signatures: Dict[str, Tuple[int, int]] = {}
        descriptions: Dict[str, str] = {}

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if result.returncode == 0:
                pattern = re.compile(r"^(\S+)\s+(.*?)\s+\((-?\d+)\|(-?\d+)\)\s*$")
                for line in result.stdout.splitlines():
                    match = pattern.match(line.rstrip())
                    if not match:
                        continue
                    name, description, nin, nout = match.groups()
                    signatures[name] = (int(nin), int(nout))
                    descriptions[name] = description.strip()
        except (subprocess.SubprocessError, OSError):
            signatures = {}
            descriptions = {}

        if signatures:
            return signatures, descriptions

        try:
            from .categories import OPERATOR_SIGNATURES
        except ImportError:
            try:
                from ..core.categories import OPERATOR_SIGNATURES
            except ImportError:
                OPERATOR_SIGNATURES = {}

        return dict(OPERATOR_SIGNATURES), {}

    @staticmethod
    def _coerce_string_list(values: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]]) -> List[str]:
        """Normalise a file/parameter value into a string list."""
        if values is None:
            return []
        if isinstance(values, (str, os.PathLike)):
            return [str(values)]
        return [str(value) for value in values]

    @staticmethod
    def _safe_path_token(value: str) -> str:
        """Create a filesystem-safe token for temporary path aliases."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "path"

    def _create_input_alias(self, original: str) -> str:
        """Create a temporary alias for an input path that contains spaces."""
        if " " not in original:
            return original

        alias_dir = Path(self._tstore.base_dir) / "cdo_path_aliases"
        alias_dir.mkdir(parents=True, exist_ok=True)
        original_path = Path(original).expanduser()
        alias_name = f"in_{time.time_ns()}_{self._safe_path_token(original_path.name)}"
        alias_path = alias_dir / alias_name

        try:
            alias_path.symlink_to(original_path)
        except OSError:
            shutil.copy2(original_path, alias_path)

        return str(alias_path)

    def _prepare_output_target(self, original: str, *, variable_output: bool) -> Tuple[str, Optional[Dict[str, str]]]:
        """Create a temporary alias for an output target if it contains spaces."""
        if " " not in original:
            return original, None

        alias_dir = Path(self._tstore.base_dir) / "cdo_path_aliases"
        alias_dir.mkdir(parents=True, exist_ok=True)
        original_path = Path(original).expanduser()
        alias_name = f"out_{time.time_ns()}_{self._safe_path_token(original_path.name)}"
        alias_path = alias_dir / alias_name

        return str(alias_path), {
            "kind": "prefix" if variable_output else "file",
            "alias": str(alias_path),
            "original": str(original_path),
        }

    def _materialise_output_aliases(self, relocations: List[Dict[str, str]]) -> None:
        """Move generated output aliases back to the user-requested paths."""
        for relocation in relocations:
            alias_path = Path(relocation["alias"])
            original_path = Path(relocation["original"])
            original_path.parent.mkdir(parents=True, exist_ok=True)

            if relocation["kind"] == "file":
                if alias_path.exists():
                    shutil.move(str(alias_path), str(original_path))
                continue

            prefix = alias_path.name
            for candidate in alias_path.parent.glob(f"{prefix}*"):
                suffix = candidate.name[len(prefix):]
                target = original_path.parent / f"{original_path.name}{suffix}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(candidate), str(target))

    def get_operator_catalog(self) -> Dict[str, Dict[str, Union[str, Tuple[int, int]]]]:
        """Return installed operator metadata keyed by operator name."""
        return {
            name: {
                "signature": self.operator_signatures[name],
                "description": self.operator_descriptions.get(name, ""),
            }
            for name in self.operator_signatures
        }

    def get_operator_signatures(self) -> Dict[str, Tuple[int, int]]:
        """Return a copy of the installed operator signature table."""
        return dict(self.operator_signatures)

    def execute_operator(
        self,
        operator: str,
        *,
        input_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        output_files: Optional[Union[str, os.PathLike, List[str], Tuple[str, ...]]] = None,
        extra_parameters: Optional[List[str]] = None,
    ) -> NCExplorerResult:
        """Execute one operator using explicit input/output/parameter groups."""
        if operator not in self.operator_signatures:
            raise ValueError(f"Unknown or unavailable CDO operator: {operator}")

        inputs = self._coerce_string_list(input_files)
        outputs = self._coerce_string_list(output_files)
        raw_parameters = [str(value) for value in (extra_parameters or [])]
        while raw_parameters and raw_parameters[-1] == "":
            raw_parameters.pop()
        parameters = raw_parameters
        nin, nout = self.operator_signatures[operator]

        if nin == -1:
            if len(inputs) < 1:
                raise ValueError(f"{operator}: expected at least 1 input file")
        elif len(inputs) != nin:
            raise ValueError(f"{operator}: expected {nin} input file(s), got {len(inputs)}")

        if nout == -1:
            if len(outputs) != 1:
                raise ValueError(f"{operator}: expected exactly 1 output prefix/base path")
        elif len(outputs) != nout:
            raise ValueError(f"{operator}: expected {nout} output target(s), got {len(outputs)}")

        aliased_inputs = [self._create_input_alias(path) for path in inputs]
        aliased_outputs: List[str] = []
        output_relocations: List[Dict[str, str]] = []
        for output in outputs:
            aliased_output, relocation = self._prepare_output_target(output, variable_output=(nout == -1))
            aliased_outputs.append(aliased_output)
            if relocation is not None:
                output_relocations.append(relocation)

        op_token = operator if not parameters else f"{operator},{','.join(parameters)}"
        cmd = [self.NCExplorer_binary, op_token, *aliased_inputs, *aliased_outputs]
        result = self._execute_command(cmd)
        if result.success and output_relocations:
            self._materialise_output_aliases(output_relocations)
        if result.success and nout == 1 and len(outputs) == 1:
            result.output_file = outputs[0]
        return result

    def _invoke_legacy_operator(self, operator: str, *args: str) -> NCExplorerResult:
        """
        Compatibility adapter for callers that still pass a flat positional argument list.
        Extra parameters are expected first, followed by input files and output files.
        """
        nin, nout = self.operator_signatures.get(operator, (1, 1))
        try:
            from .categories import OPERATOR_SCHEMA
        except ImportError:
            try:
                from ..core.categories import OPERATOR_SCHEMA  # type: ignore
            except ImportError:
                OPERATOR_SCHEMA = {}  # type: ignore
        spec = OPERATOR_SCHEMA.get(operator) if OPERATOR_SCHEMA else None
        if spec is not None:
            n_extra = len(spec.params)
        else:
            n_extra = self.EXTRA_PARAM_COUNTS.get(operator, 0)
        # Respect legacy callers that pass fewer extras (e.g. one combined
        # "val1,val2" string) than the schema technically expects. Cap n_extra
        # so we always leave room for the required file args.
        if nin == -1:
            min_files = 1 if nout == -1 else max(1, nout)
        else:
            min_files = nin + (1 if nout == -1 else max(0, nout))
        if n_extra > 0 and len(args) - n_extra < min_files:
            n_extra = max(0, len(args) - min_files)
        extra_parameters = [str(value) for value in args[:n_extra]]
        file_args = [str(value) for value in args[n_extra:]]

        if nin == -1:
            if nout == 0:
                inputs = file_args
                outputs: List[str] = []
            elif nout == -1:
                inputs = file_args[:-1]
                outputs = file_args[-1:]
            else:
                inputs = file_args[:-nout]
                outputs = file_args[-nout:]
        else:
            inputs = file_args[:nin]
            if nout == 0:
                outputs = []
            elif nout == -1:
                outputs = file_args[nin:]
            else:
                outputs = file_args[nin:nin + nout]

        return self.execute_operator(
            operator,
            input_files=inputs,
            output_files=outputs,
            extra_parameters=extra_parameters,
        )

    def _build_signature_aware_method(self, operator: str) -> Callable[..., NCExplorerResult]:
        """Create a bound operator method that delegates to the explicit executor."""
        def _method(*args: str) -> NCExplorerResult:
            return self._invoke_legacy_operator(operator, *args)

        return _method

    def _find_NCExplorer_binary(self) -> Optional[str]:
        """Try to find cdo binary in common locations."""
        # Common paths where cdo might be installed
        common_paths = [
            '/usr/bin/cdo',
            '/usr/local/bin/cdo',
            '/opt/local/bin/cdo',  # MacPorts
            '/sw/bin/cdo',  # Fink
            '/opt/homebrew/bin/cdo',  # Homebrew on Apple Silicon
            '/usr/local/Cellar/cdo/*/bin/cdo',  # Homebrew pattern
        ]

        # Try using the 'which' command
        try:
            result = subprocess.run(['which', 'cdo'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logging.debug("'which cdo' discovery failed: %s", e)

        # Try using the 'whereis' command on Linux
        if self.platform == "linux":
            try:
                result = subprocess.run(['whereis', 'cdo'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    parts = result.stdout.strip().split()
                    if len(parts) > 1:
                        return parts[1]  # First path after "cdo"
            except (subprocess.SubprocessError, FileNotFoundError) as e:
                logging.debug("'whereis cdo' discovery failed: %s", e)

        # Check common installation paths
        for path in common_paths:
            if '*' in path:
                # Handle glob patterns
                from glob import glob
                matches = glob(path)
                if matches:
                    return matches[0]
            else:
                if os.path.isfile(path) and os.access(path, os.X_OK):
                    return path

        return None

    def _get_installation_help(self) -> str:
        """Generate a helpful error message with installation instructions."""
        base_msg = f"cdo binary not found on {self.platform}: {self.NCExplorer_binary}"

        if self.platform == "darwin":  # macOS
            help_msg = """
    cdo Installation Instructions for macOS:

    1. Using Homebrew (recommended):
       brew install cdo

    2. Using MacPorts:
       sudo port install cdo

    3. Manual installation:
       Download from https://code.mpimet.mpg.de/projects/cdo

    After installation, cdo is typically available at:
    - /usr/local/bin/cdo (Homebrew)
    - /opt/local/bin/cdo (MacPorts)
    """
        elif self.platform == "linux":
            help_msg = """
    cdo Installation Instructions for Linux:

    1. Ubuntu/Debian:
       sudo apt-get update
       sudo apt-get install cdo

    2. CentOS/RHEL/Fedora:
       sudo yum install cdo
       # or
       sudo dnf install cdo

    3. From source:
       Download from https://code.mpimet.mpg.de/projects/cdo

    After installation, cdo is typically available at:
    - /usr/bin/cdo
    - /usr/local/bin/cdo
    """
        else:
            help_msg = f"""
    Please install cdo for your platform ({self.platform}).
    Visit: https://code.mpimet.mpg.de/projects/cdo
    """

        return base_msg + help_msg

    def __getattr__(self, name: str) -> Callable[..., NCExplorerResult]:
        """Create operator methods lazily when they are requested."""
        if name in self.operator_signatures:
            method = self._build_signature_aware_method(name)
            setattr(self, name, method)
            return method
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    # -------------------------------------------------------------------------
    # Private helpers for platform detection and command execution
    # -------------------------------------------------------------------------

    def _init_windows_NCExplorer(self, use_wsl: Optional[bool]) -> bool:
        if use_wsl is None:
            return self._auto_detect_windows_NCExplorer()
        if not self._test_NCExplorer_availability(use_wsl):
            method = "WSL" if use_wsl else "native Windows"
            raise NCExplorerError(f"cdo binary not found using {method} method: {self.NCExplorer_binary}")
        return use_wsl

    def _auto_detect_windows_NCExplorer(self) -> bool:
        if self._test_NCExplorer_availability(use_wsl=False):
            self.logger.info("Using native Windows cdo")
            return False
        if self._test_NCExplorer_availability(use_wsl=True):
            self.logger.info("Using WSL cdo")
            return True
        raise NCExplorerError("cdo binary not found in native Windows or WSL environment")

    # Verification helpers
    def _verify_unix_NCExplorer(self) -> bool:
        return self._test_NCExplorer_availability(use_wsl=False)

    def _test_NCExplorer_availability(self, use_wsl: bool) -> bool:
        try:
            cmd = ["wsl", self.NCExplorer_binary, "--version"] if use_wsl else [self.NCExplorer_binary, "--version"]
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).returncode == 0
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

    # Command building
    def _build_command(self, NCExplorer_cmd: List[str]) -> List[str]:
        if self.platform == "windows" and self.use_wsl:
            converted = [self._win_to_wsl(arg) if self._is_file_path(arg) else arg for arg in NCExplorer_cmd]
            return ["wsl"] + converted
        return NCExplorer_cmd

    # Path utilities
    @staticmethod
    def _is_file_path(arg: str) -> bool:
        if not isinstance(arg, str):  # Ensure we only process strings
            return False
        climate_ext = (".nc", ".grb", ".grib", ".grib2", ".hdf", ".h5")
        return arg.endswith(climate_ext) or "/" in arg or "\\" in arg or (len(arg) > 1 and arg[1] == ":")

    @staticmethod
    def _win_to_wsl(path: PathLike) -> str:
        path_str = str(path)
        if len(path_str) > 1 and path_str[1] == ":":
            drive, rest = path_str[0].lower(), path_str[2:].replace("\\", "/")
            return f"/mnt/{drive}{rest}"
        return path_str.replace("\\", "/")

    @staticmethod
    def _validate_NCExplorer_arguments(*args):
        """Validate that all arguments are proper strings, not widget objects"""
        for i, arg in enumerate(args):
            if hasattr(arg, '__class__') and 'PyQt' in str(arg.__class__):
                raise ValueError(f"Argument {i} is a PyQt widget object instead of a string: {arg}")
            if not isinstance(arg, str):
                raise ValueError(f"Argument {i} must be a string, got {type(arg)}: {arg}")

    def _execute_command(self, cmd: List[str], timeout: int = 300) -> NCExplorerResult:
        # Add validation and debug logging
        if len(cmd) > 1:
            self._validate_NCExplorer_arguments(*cmd[1:])

        # Debug logging
        self.logger.info(f"Executing NCExplorer command: {' '.join(cmd)}")

        start = time.time()
        try:
            full_cmd = self._build_command(cmd)
            self.logger.info(f"Full command with platform adjustments: {' '.join(full_cmd)}")
            self.last_command = " ".join(full_cmd)
            self.print_last_cdo_command()

            outcome: subprocess.CompletedProcess[str] = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._tstore.base_dir)
            )

            # Determine success based on operator type and output
            success = self._determine_command_success(cmd, outcome)

            result = NCExplorerResult(
                success=success,
                stdout=outcome.stdout,
                stderr=outcome.stderr,
                execution_time=time.time() - start,
            )

            # Debug logging for results
            self.logger.info(f"Command completed with return code: {outcome.returncode}")
            if outcome.stdout:
                self.logger.info(f"STDOUT: {outcome.stdout[:500]}...")
            if outcome.stderr:
                self.logger.error(f"STDERR: {outcome.stderr}")

            return result

        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out after {timeout} seconds")
            return NCExplorerResult(False, "", f"Command timed out after {timeout} seconds")
        except Exception as exc:
            self.logger.error(f"Command execution failed: {str(exc)}")
            return NCExplorerResult(False, "", str(exc))

    def _determine_command_success(self, cmd: List[str], outcome: subprocess.CompletedProcess) -> bool:
        """
        Determine if a cdo command succeeded based on operator type and output.

        Some operators like 'diff' return non-zero exit codes for valid results.
        """
        if len(cmd) < 2:
            return outcome.returncode == 0

        operator = cmd[1].split(",", 1)[0]
        nin, nout = self.operator_signatures.get(operator, (1, 1))

        # Special handling for comparison/diff operators
        if operator in ['diff', 'diffv', 'diffc', 'diffn', 'diffp']:
            # For diff operators:
            # - Exit code 0: files are identical
            # - Exit code 1: differences found (this is SUCCESS, not failure)
            # - Exit code >1: actual error
            if outcome.returncode <= 1 and outcome.stdout:
                return True
            elif outcome.returncode > 1:
                return False
            else:
                # No output but exit code 0 or 1 - probably an error
                return outcome.returncode == 0

        # Special handling for info operators (nout=0)
        elif nout == 0:
            # Info operators succeed if they produce output OR return code 0
            return outcome.returncode == 0 or bool(outcome.stdout.strip())

        # Special handling for validation/check operators
        elif operator in ['checkfile', 'verify', 'validate']:
            # These might return non-zero for "validation failed" vs. "error occurred"
            # Accept exit codes 0-1 if there's meaningful output
            return outcome.returncode <= 1 and (outcome.stdout or outcome.returncode == 0)

        # Default case: only exit code 0 is success
        return outcome.returncode == 0

    # -------------------------------------------------------------------------
    # Dynamic operator method factory
    # -------------------------------------------------------------------------

    def _generate_operator_methods(self) -> None:
        """
        Create one Python method per cdo operator using the signature
        published by `cdo --operators` (numbers in brackets: input|output).

        Legend
        -------
        Nin  =  -1  …  variable number of input files   (>=1)
                0   …  no input files
                1   …  exactly one input file
                2   …  exactly two input files
                3   …  exactly three input files
                …   etc.

        Nout =  -1  …  variable number of output files (>=1)
                0   …  no output file – the command prints to stdout
                1   …  exactly one output file
                …   etc.
        """

        for op_name in self.operator_signatures:
            setattr(self, op_name, self._build_signature_aware_method(op_name))

    # -------------------------------------------------------------------------
    # Generic utilities
    # -------------------------------------------------------------------------
    def get_operator_syntax(self, operator: str) -> str:
        """
        Retrieves the syntax (and possibly a brief description) for a given cdo operator
        by calling `CDO -h <operator>`.

        Args:
            operator: The name of the CDO operator (e.g., "sinfo", "selvar").

        Returns:
            A string containing the operator's syntax and description, or an error message.
        """
        try:
            # We use _execute_command directly here as we don't need a temporary output file
            # and want raw stdout/stderr for parsing help.
            cmd = self._build_command([self.NCExplorer_binary, "-h", operator])
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                encoding='utf-8'
            )

            if process.returncode == 0:
                # CDO help output format often has the syntax line near the top.
                # This parsing is heuristic and might need adjustment for specific CDO versions.
                lines = process.stdout.splitlines()
                syntax_info = []
                for line in lines:
                    line_stripped = line.strip()
                    if line_stripped.startswith(operator):
                        syntax_info.append(line_stripped)
                    elif syntax_info and line_stripped:  # Add subsequent description lines
                        syntax_info.append(line_stripped)
                    if len(syntax_info) > 5 and not line_stripped:  # Limit description
                        break

                if syntax_info:
                    return "\n".join(syntax_info)
                else:
                    return f"Syntax for '{operator}' not found in cdo help. Full output:\n{process.stdout}"
            else:
                logging.warning(f"cdo help call failed for '{operator}'. Stderr: {process.stderr}")
                return f"Could not retrieve syntax for '{operator}'. cdo error: {process.stderr}"
        except FileNotFoundError:
            return "cdo binary not found. Cannot retrieve operator syntax."
        except Exception as e:
            logging.error(f"Error retrieving cdo syntax for '{operator}': {e}")
            return f"An error occurred while fetching syntax for '{operator}': {e}"

    def get_execution_info(self) -> Dict[str, str]:
        return {
            "platform": self.platform,
            "NCExplorer_binary": self.NCExplorer_binary,
            "execution_method": "WSL" if self.platform == "windows" and self.use_wsl else "native",
            "temp_dir": self.temp_dir,
        }

    def print_last_cdo_command(self) -> str:
        """Prints and returns the last CDO command that was executed."""
        if self.last_command:
            print(f"Last CDO command:\n  {self.last_command}")
        else:
            print("No CDO command has been run yet.")
        return self.last_command

    def get_NCExplorer_version(self) -> NCExplorerResult:
        return self._execute_command([self.NCExplorer_binary, "--version"])

    def get_temp_filename(self, suffix: str = ".nc") -> str:
        if not suffix.startswith("."):
            suffix = "." + suffix
        return os.path.join(self.temp_dir, f"NCExplorer_temp_{os.getpid()}{suffix}")

    def cleanup_temp_files(self, pattern: str = "NCExplorer_temp_*") -> None:
        for fp in Path(self.temp_dir).glob(pattern):
            try:
                fp.unlink()
            except OSError:
                pass

    def __del__(self) -> None:
        self._tstore.cleanup()


# -----------------------------------------------------------------------------#
# Convenience factory functions
# -----------------------------------------------------------------------------#

def create_NCExplorer_integration(NCExplorer_binary_path: str = "cdo",
                           temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Automatic selection (native vs. WSL on Windows)."""
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path, temp_dir=temp_dir)


def create_native_NCExplorer(NCExplorer_binary_path: str = "cdo",
                      temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Force native execution (helpful on Windows when WSL is unwanted)."""
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path,
                          temp_dir=temp_dir, use_wsl=False)


def create_wsl_NCExplorer(NCExplorer_binary_path: str = "cdo",
                   temp_dir: Optional[str] = None) -> NCExplorerIntegration:
    """Force WSL execution (Windows only)."""
    if platform.system().lower() != "windows":
        raise NCExplorerError("WSL execution is only valid on Windows hosts")
    return NCExplorerIntegration(NCExplorer_binary_path=NCExplorer_binary_path,
                          temp_dir=temp_dir, use_wsl=True)
