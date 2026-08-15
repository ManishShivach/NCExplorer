"""Tests for the CDO integration layer itself.

Three layers, cheapest first:

* **Mocked** — ``NCExplorerIntegration`` builds the right command line, applies
  the right signature rules and reports failures faithfully, with no CDO
  involved. These run anywhere.
* **Catalog** — the operator schema, the generated CDO catalog it is composed
  from, and the operator-testing profiles agree with one another. There is no
  hand-curated signature table to check any more: ``OPERATOR_SIGNATURES`` held
  716 of the catalog's 943 and every surface now reads arity from
  ``OPERATOR_SCHEMA`` through ``get_operator_spec``, so what these tests assert
  is that the one remaining source says the same thing everywhere it is read.
* **Live** — the resolved CDO actually answers, its catalog loads, and one
  operator runs end to end. Skipped automatically when no CDO is installed.

This is the "does the integration work" half. The "do the 943 operators work"
half is ``test_all_operators.py`` (command line) and ``testCDOcommands.py``
(pick operators, press Run); both drive the same ``operator_lab`` harness that
the live tests below use, so a green run here is the precondition for trusting
a sweep there.

    python testCDO.py                    # everything
    python testCDO.py TestLiveIntegration  # just the live checks
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typing import get_args

from ncexplorer_toolkit.core.categories import (
    OPERATOR_SCHEMA, ParamKind, get_operator_spec,
    operator_required_param_count, operator_total_param_count,
)
from ncexplorer_toolkit.core.cdo_operator_catalog import CDO_OPERATORS
from ncexplorer_toolkit.core.nc_integration import (
    NCExplorerError, NCExplorerIntegration, NCExplorerResult,
    create_native_NCExplorer, create_NCExplorer_integration,
    create_wsl_NCExplorer,
)

HAS_CDO = shutil.which("cdo") is not None
needs_cdo = unittest.skipUnless(HAS_CDO, "needs an installed CDO")


class TestNCExplorerResult(unittest.TestCase):
    """The result record and its truthiness."""

    def test_success_result(self):
        result = NCExplorerResult(
            success=True, stdout="Test output", stderr="",
            output_file="/tmp/test.nc", execution_time=1.5,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "Test output")
        self.assertEqual(result.output_file, "/tmp/test.nc")
        self.assertEqual(result.execution_time, 1.5)
        self.assertTrue(bool(result))

    def test_failure_result(self):
        result = NCExplorerResult(success=False, stdout="", stderr="Error occurred")
        self.assertFalse(result.success)
        self.assertFalse(bool(result))
        self.assertEqual(result.stderr, "Error occurred")


class TestNCExplorerError(unittest.TestCase):
    """The exception carries enough to diagnose a failure without the logs."""

    def test_error_fields(self):
        error = NCExplorerError(
            "Test error", stdout="Some output", stderr="Error details", returncode=1,
        )
        self.assertEqual(str(error), "(returncode:1) Error details")
        self.assertEqual(error.stdout, "Some output")
        self.assertEqual(error.stderr, "Error details")
        self.assertEqual(error.returncode, 1)


class TestNCExplorerIntegration(unittest.TestCase):
    """Platform resolution and command execution, with CDO mocked out."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch("platform.system")
    def test_linux_initialization(self, mock_system, mock_run):
        mock_system.return_value = "Linux"
        mock_run.return_value = subprocess.CompletedProcess(
            args=["cdo", "--version"], returncode=0, stdout="cdo version 2.6.0",
        )

        integration = NCExplorerIntegration(
            temp_dir=self.temp_dir, auto_find_NCExplorer=False)

        self.assertEqual(integration.platform, "linux")
        self.assertFalse(integration.use_wsl)
        self.assertEqual(integration.NCExplorer_binary, "cdo")

    @patch("subprocess.run")
    def test_execute_command_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["cdo", "--version"], returncode=0,
            stdout="cdo version 2.6.0", stderr="",
        )

        with patch("platform.system", return_value="Linux"), \
                patch.object(NCExplorerIntegration, "_verify_unix_NCExplorer",
                             return_value=True):
            integration = NCExplorerIntegration(
                temp_dir=self.temp_dir, auto_find_NCExplorer=False)
            result = integration._execute_command(["cdo", "--version"])

        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "cdo version 2.6.0")


class TestDynamicOperatorMethods(unittest.TestCase):
    """The per-operator methods generated from the signature table."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        with patch("platform.system", return_value="Linux"), \
                patch("subprocess.run") as mock_run, \
                patch.object(NCExplorerIntegration, "_verify_unix_NCExplorer",
                             return_value=True):
            mock_run.return_value = subprocess.CompletedProcess(
                args=["cdo", "--version"], returncode=0, stdout="cdo 2.6.0",
            )
            self.integration = NCExplorerIntegration(
                temp_dir=self.temp_dir, auto_find_NCExplorer=False)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("subprocess.run")
    def test_info_operator_has_no_output_file(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Info output here", stderr="")

        result = self.integration.info("test.nc")

        self.assertTrue(result.success)
        self.assertEqual(result.stdout, "Info output here")
        self.assertIsNone(result.output_file)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_diff_operator_has_no_output_file(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Difference output here", stderr="")

        result = self.integration.diff("file1.nc", "file2.nc")

        self.assertTrue(result.success)
        self.assertIsNone(result.output_file)

    @patch("subprocess.run")
    def test_standard_operator_reports_its_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Processing complete", stderr="")

        result = self.integration.timavg("input.nc", "output.nc")

        self.assertTrue(result.success)
        self.assertEqual(result.output_file, "output.nc")
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_multi_input_operator(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Concatenation complete", stderr="")

        result = self.integration.cat("file1.nc", "file2.nc", "file3.nc", "output.nc")

        self.assertTrue(result.success)
        self.assertEqual(result.output_file, "output.nc")

    @patch("subprocess.run")
    def test_split_operator_reports_no_single_output(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Split complete", stderr="")

        result = self.integration.splityear("input.nc", "prefix")

        self.assertTrue(result.success)
        # Many files were written; naming one of them would be a lie.
        self.assertIsNone(result.output_file)

    @patch("subprocess.run")
    def test_generator_operator_needs_no_input(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Constant field created", stderr="")

        result = self.integration.const("273.15", "r360x180", "output.nc")

        self.assertTrue(result.success)
        self.assertEqual(result.output_file, "output.nc")
        self.assertEqual(self.integration.last_command,
                         "cdo const,273.15,r360x180 output.nc")

    def test_one_argument_per_declared_parameter(self):
        """Two values for a two-parameter operator, not one combined string.

        ``_invoke_legacy_operator`` still caps the number of extras it peels so
        that a caller passing ``"273.15,r360x180"`` leaves room for the output
        file, and that cap dates from before parameters were validated. The
        list now reaches ``_require_parameters``, which counts it against the
        schema and refuses one value where ``const`` declares two.

        Refusing is right, and the reason is worth pinning: the validator
        cannot tell a deliberately combined pair from a genuinely missing grid,
        and it must not split on the comma to find out — ``selseas``'s season
        list and ``outputtab``'s column list are each one parameter whose value
        contains commas. So the convention is one argument per declared
        parameter, and the combined form is a ``ValueError`` rather than a
        command built on a guess.
        """
        with self.assertRaises(ValueError) as caught:
            self.integration.const("273.15,r360x180", "output.nc")

        self.assertIn("grid", str(caught.exception))

    def test_wrong_input_count_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.integration.diff("only_one_file.nc")

        # Matched loosely on purpose: the exact wording has changed once
        # already ("expected 2 input files" → "expected 2 input file(s), got 1")
        # and a test that pins the phrasing fails on an improvement to it.
        message = str(caught.exception)
        self.assertIn("diff", message)
        self.assertIn("2 input file", message)

    def test_variable_input_operator_accepts_one_file(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr="")
            self.integration.info("file.nc")  # must not raise

    @patch("subprocess.run")
    def test_failure_is_reported_not_raised(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="File not found: nonexistent.nc")

        result = self.integration.timavg("nonexistent.nc", "output.nc")

        self.assertFalse(result.success)
        self.assertIn("File not found", result.stderr)


class TestFactoryFunctions(unittest.TestCase):
    """The three constructors pick the execution method they promise."""

    @patch("platform.system")
    @patch("subprocess.run")
    def test_auto_integration(self, mock_run, mock_system):
        mock_system.return_value = "Linux"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="cdo 2.6.0")

        integration = create_NCExplorer_integration()

        self.assertIsInstance(integration, NCExplorerIntegration)
        self.assertEqual(integration.platform, "linux")

    @patch("platform.system")
    @patch("subprocess.run")
    def test_native_integration(self, mock_run, mock_system):
        mock_system.return_value = "Windows"
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="cdo 2.6.0")

        integration = create_native_NCExplorer()

        self.assertIsInstance(integration, NCExplorerIntegration)
        self.assertEqual(integration.platform, "windows")
        self.assertFalse(integration.use_wsl)

    @patch("platform.system")
    def test_wsl_refused_off_windows(self, mock_system):
        mock_system.return_value = "Linux"
        with self.assertRaises(NCExplorerError):
            create_wsl_NCExplorer()


class TestOperatorArity(unittest.TestCase):
    """Arity comes from the schema, and from nothing else.

    A hand-written ``OPERATOR_SIGNATURES`` table used to answer this, and it is
    gone. It held 716 of the catalog's 943, and the three call sites that read
    it fell back to ``(1, 1)`` for the rest — so ``timcor``, ``timcovar`` and
    the twelve ``ymon``/``yseas`` comparison operators each drew one input row
    and reached CDO as ``cdo timcor in.nc out.nc``. What replaced it is one
    lookup, and the tests below are about that lookup rather than about a
    table's shape: every operator has a spec, arity is readable through
    ``get_operator_spec`` for all 943, and the multi-input operators that the
    fallback used to mangle are asked about by name.
    """

    #: The operators the removed table did not list. Named individually because
    #: a count would pass while the specific regression returned.
    ONCE_MISSING = {
        "timcor": 2, "timcovar": 2, "varrms": 2, "wct": 2, "subtrend": 3,
        "ymonsub": 2, "yseasadd": 2, "ymonadd": 2, "yseassub": 2,
    }

    def test_every_operator_has_a_spec(self):
        self.assertEqual(len(OPERATOR_SCHEMA), len(CDO_OPERATORS))
        for name in CDO_OPERATORS:
            self.assertIsNotNone(get_operator_spec(name), name)

    def test_arity_is_well_formed(self):
        for name, spec in OPERATOR_SCHEMA.items():
            self.assertIsInstance(spec.nin, int, name)
            self.assertIsInstance(spec.nout, int, name)
            self.assertGreaterEqual(spec.nin, -1, name)
            self.assertGreaterEqual(spec.nout, -1, name)

    def test_the_operators_the_old_table_omitted_declare_their_inputs(self):
        for name, nin in self.ONCE_MISSING.items():
            spec = get_operator_spec(name)
            self.assertIsNotNone(spec, name)
            self.assertEqual(spec.nin, nin,
                             f"{name} would draw the wrong number of input rows")

    def test_a_missing_operator_answers_none_rather_than_a_default(self):
        """The failure mode of the old table was a plausible wrong answer.

        ``get_operator_spec`` returns ``None`` for a name it does not know, so
        a caller has to decide what to do about it rather than being handed
        ``(1, 1)`` and carrying on.
        """
        self.assertIsNone(get_operator_spec("no_such_operator"))
        self.assertEqual(operator_required_param_count("no_such_operator"), 0)
        self.assertEqual(operator_total_param_count("no_such_operator"), 0)

    def test_parameter_counts_come_from_the_same_spec(self):
        for name, spec in OPERATOR_SCHEMA.items():
            self.assertEqual(operator_total_param_count(name), len(spec.params),
                             name)
            self.assertEqual(
                operator_required_param_count(name),
                sum(1 for p in spec.params if not p.optional), name)
            self.assertLessEqual(operator_required_param_count(name),
                                 operator_total_param_count(name), name)


class TestIntegrationWithGUI(unittest.TestCase):
    """What the GUI reads off the integration."""

    def setUp(self):
        with patch("platform.system", return_value="Linux"), \
                patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="cdo 2.6.0")
            self.integration = create_NCExplorer_integration()

    def test_execution_info_fields(self):
        info = self.integration.get_execution_info()
        for key in ("platform", "NCExplorer_binary", "execution_method", "temp_dir"):
            self.assertIn(key, info)

    @patch("subprocess.run")
    def test_version_query(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Climate Data Operators version 2.6.0", stderr="")

        result = self.integration.get_NCExplorer_version()

        self.assertTrue(result.success)
        self.assertIn("Climate Data Operators", result.stdout)

    def test_temp_file_management(self):
        temp_file = self.integration.get_temp_filename(".grb")
        self.assertTrue(temp_file.endswith(".grb"))
        self.assertIn("NCExplorer_temp_", temp_file)
        self.integration.cleanup_temp_files()  # must not raise


class TestOperatorSchemaConsistency(unittest.TestCase):
    """``OPERATOR_SCHEMA`` against the catalog it is built from.

    ``cdo_operator_catalog.CDO_OPERATORS`` carries the signature and short
    description of every operator the binary publishes, and the schema is
    composed from it. Equality is therefore the right assertion here, and it is
    the one the old signature table could never support: it held a subset, so
    the only checkable claim was that the two agreed where they overlapped.
    """

    #: Taken from ``ParamKind`` itself rather than written out. A hand-copied
    #: set is how this test came to be checking six kinds against a union of
    #: nine — ``bool``, ``multiselect`` and ``expression`` were added to the
    #: schema and never here, so 137 declared parameters would have failed a
    #: test whose point is that the GUI can render all of them.
    VALID_KINDS = set(get_args(ParamKind))

    def test_the_schema_is_exactly_the_catalog(self):
        self.assertEqual(set(OPERATOR_SCHEMA), set(CDO_OPERATORS))

    def test_every_operator_agrees_with_the_catalog_on_shape(self):
        conflicts = [
            (name, (nin, nout), (OPERATOR_SCHEMA[name].nin,
                                 OPERATOR_SCHEMA[name].nout))
            for name, (nin, nout, _desc) in CDO_OPERATORS.items()
            if (OPERATOR_SCHEMA[name].nin, OPERATOR_SCHEMA[name].nout)
            != (nin, nout)
        ]
        self.assertEqual(conflicts, [], "schema and catalog disagree on arity")

    def test_param_kinds_are_renderable(self):
        for name, spec in OPERATOR_SCHEMA.items():
            for param in spec.params:
                self.assertIn(param.kind, self.VALID_KINDS,
                              f"{name}: param '{param.name}' kind '{param.kind}'")
                self.assertIsInstance(param.name, str)
                self.assertTrue(param.name, f"{name}: param has an empty name")

    def test_param_grammars_are_spellable(self):
        """``form`` decides how a value joins the operator token.

        Three grammars, and the wrong one is not reliably an error — CDO takes
        ``splitmon,format=%B`` and writes ``monformat=January.nc`` — so an
        unknown value here would reach argv silently.
        """
        for name, spec in OPERATOR_SCHEMA.items():
            for param in spec.params:
                self.assertIn(param.form,
                              {"positional", "keyword", "flag"},
                              f"{name}: param '{param.name}' form '{param.form}'")

    def test_a_closed_vocabulary_offers_something_to_choose(self):
        for name, spec in OPERATOR_SCHEMA.items():
            for param in spec.params:
                if param.kind in ("select", "multiselect"):
                    self.assertTrue(
                        param.choices,
                        f"{name}: '{param.name}' is a {param.kind} with no choices")

    def test_every_spec_is_complete(self):
        for name, spec in OPERATOR_SCHEMA.items():
            self.assertEqual(spec.name, name)
            self.assertIsNotNone(spec.category, f"{name}: missing category")
            self.assertIsInstance(spec.params, tuple)


class TestOperatorProfiles(unittest.TestCase):
    """The testing profiles the sweep runs on."""

    def test_informational_operators_produce_text(self):
        from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA
        from operator_lab import preferred_output_extension

        for name, spec in OPERATOR_SCHEMA.items():
            if spec.nout == 0 and name not in {
                "gradsdes", "gmtcells", "gmtxyz", "outputbounds",
                "outputboundscpt", "outputcenter", "outputcenter2",
                "outputcentercpt", "outputtri", "outputkml", "outputvrml",
                "outputsrv", "outputext",
            }:
                self.assertEqual(preferred_output_extension(name), ".txt", name)

    def test_generators_declare_no_input(self):
        from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA
        from operator_lab import preferred_input_extension

        self.assertEqual(preferred_input_extension("random"), "—")
        self.assertEqual(preferred_input_extension("const"), "—")
        for name, spec in OPERATOR_SCHEMA.items():
            if spec.nin == 0:
                self.assertEqual(preferred_input_extension(name), "—", name)

    def test_ordinary_operators_read_and_write_netcdf(self):
        from operator_lab import (
            preferred_input_extension, preferred_output_extension,
        )

        for name in ("timmean", "selname", "remapbil", "copy", "expr"):
            self.assertEqual(preferred_input_extension(name), ".nc", name)
            self.assertEqual(preferred_output_extension(name), ".nc", name)

    def test_every_skip_explains_itself(self):
        from operator_lab import UNTESTABLE

        for name, reason in UNTESTABLE.items():
            self.assertTrue(reason.strip(), f"{name}: empty skip reason")
            self.assertGreater(len(reason), 15, f"{name}: reason says too little")

    #: Required parameters the harness synthesises rather than defaults, keyed
    #: by the kind that gets synthesised. ``file`` and ``grid`` are filled in by
    #: ``_resolve_parameters``; ``expression`` joins them for the two operators
    #: whose script is a path — ``exprf``/``aexprf`` declare kind
    #: ``expression`` so the GUI opens the editor, but the value is a filename
    #: and the harness writes a script there. Counting those as missing is what
    #: this test used to do, and it named two operators that run perfectly well.
    SYNTHESISED_KINDS = ("file", "grid")

    #: The required parameters that genuinely have no default, with the reason.
    #: Listed rather than tolerated by rule, so that a *new* gap fails this test
    #: instead of joining a silent category.
    #:
    #: Both are ``outputtab``'s eighteen-keyname grammar, where the value is a
    #: chosen set of columns in a chosen order. There is no wrong answer to
    #: default to, and the sweep reports both as skipped for
    #: "no default for the required parameter 'keynames'" — the one place a
    #: skip is a fact about this harness rather than about the data.
    KNOWN_WITHOUT_DEFAULT = {"outputtab.keynames", "outputkey.keynames"}

    def test_required_parameters_all_have_defaults(self):
        """A sweep should not skip an operator merely for want of a value.

        Every required parameter the harness does not synthesise needs either a
        shared default or a per-operator one; without it the operator is
        reported as skipped, which reads as a gap in the catalog rather than a
        gap here.
        """
        from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA
        from ncexplorer_toolkit.core.expr_reference import reads_from_file
        from operator_lab.profiles import OPERATOR_PARAMETERS, PARAMETER_DEFAULTS

        missing = []
        for name, spec in OPERATOR_SCHEMA.items():
            for param in spec.params:
                kind = param.kind
                if kind == "expression" and reads_from_file(name):
                    kind = "file"
                if param.optional or kind in self.SYNTHESISED_KINDS:
                    continue
                if not (OPERATOR_PARAMETERS.get(name, {}).get(param.name)
                        or PARAMETER_DEFAULTS.get(param.name)):
                    missing.append(f"{name}.{param.name}")

        self.assertEqual(set(missing), self.KNOWN_WITHOUT_DEFAULT,
                         "parameters with no default value")


@needs_cdo
class TestLiveIntegration(unittest.TestCase):
    """The resolved CDO answers, and one operator runs end to end.

    These are the checks the sweep's preflight makes, run as tests: if any of
    them fails, every per-operator result in a report is meaningless.
    """

    @classmethod
    def setUpClass(cls):
        cls.integration = create_NCExplorer_integration()
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="cdo_live_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_version_is_reported(self):
        result = self.integration.get_NCExplorer_version()
        self.assertTrue(result.success, result.stderr)
        self.assertIn("Climate Data Operators", result.stdout + result.stderr)

    def test_operator_catalog_loads_from_the_binary(self):
        catalog = self.integration.get_operator_catalog()
        self.assertGreater(len(catalog), 500,
                           "the catalog fell back to the static table")
        for operator in ("timmean", "selname", "info", "remapbil"):
            self.assertIn(operator, catalog)

    def test_preflight_and_a_real_sweep(self):
        from operator_lab import PASS, OperatorTestRunner, SampleSet

        samples = SampleSet.generate(self.temp_dir / "samples",
                                     binary=self.integration.NCExplorer_binary)
        runner = OperatorTestRunner(
            self.integration, samples, self.temp_dir / "out", timeout=60)

        for check in runner.preflight():
            self.assertTrue(check.ok, f"{check.name}: {check.detail}")

        # A representative handful: a statistic, a selection, a stdout-only
        # operator and a generator. If these four cannot run, nothing can.
        for operator in ("timmean", "selname", "info", "random"):
            outcome = runner.run(operator)
            self.assertEqual(outcome.status, PASS,
                             f"{operator}: {outcome.why}\n{outcome.command}")

    def test_informational_output_is_captured_as_text(self):
        from operator_lab import PASS, OperatorTestRunner, SampleSet

        samples = SampleSet.generate(self.temp_dir / "samples",
                                     binary=self.integration.NCExplorer_binary)
        runner = OperatorTestRunner(
            self.integration, samples, self.temp_dir / "out_text", timeout=60)

        outcome = runner.run("showname")

        self.assertEqual(outcome.status, PASS, outcome.why)
        self.assertTrue(outcome.output_path.endswith(".txt"), outcome.output_path)
        self.assertTrue(Path(outcome.output_path).is_file())
        self.assertIn(samples.variable, Path(outcome.output_path).read_text())


@needs_cdo
class TestOperatorSurfaces(unittest.TestCase):
    """Every installed operator is reachable from all three pickers.

    ``test/tests_ui/test_operator_parity.py`` covers this from the app's side;
    here it guards the report's surface columns, which are only worth reading if
    the scan they come from actually walked the widgets.
    """

    @classmethod
    def setUpClass(cls):
        from operator_lab import scan

        cls.scan = scan(create_NCExplorer_integration())

    def test_surfaces_were_inspected(self):
        self.assertEqual(self.scan.errors, {})
        self.assertGreater(len(self.scan.installed), 500)

    def test_all_surfaces_agree(self):
        self.assertEqual(self.scan.disagreements, [])

    def test_a_known_operator_reports_its_placement(self):
        surfaces = self.scan.get("timmean")
        self.assertTrue(surfaces.toolbar)
        self.assertTrue(surfaces.palette)
        self.assertTrue(surfaces.model_builder)
        self.assertTrue(surfaces.toolbar_category)
        self.assertTrue(surfaces.reachable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
