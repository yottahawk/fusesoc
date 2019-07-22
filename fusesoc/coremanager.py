import logging
import os

from okonomiyaki.versions import EnpkgVersion

from simplesat.constraints import PrettyPackageStringParser, Requirement
from simplesat.dependency_solver import DependencySolver
from simplesat.errors import NoPackageFound, SatisfiabilityError
from simplesat.pool import Pool
from simplesat.repository import Repository
from simplesat.request import Request

from fusesoc.core import Core

logger = logging.getLogger(__name__)

class DependencyError(Exception):
    def __init__(self, value, msg=""):
        self.value = value
        self.msg = msg
    def __str__(self):
        return repr(self.value)

class CoreDB(object):
    """ Stores all loaded Core() objects

    This can be both CAPI1 and CAPI2 objects simultaneously
    The database object is a list, 'self._cores'
    """

    def __init__(self):
        self._cores = {}

    #simplesat doesn't allow ':', '-' or leading '_'
    def _package_name(self, vlnv):
        """ Returns a vln(v) string """
        _name = "{}_{}_{}".format(vlnv.vendor,
                                  vlnv.library,
                                  vlnv.name).lstrip("_")
        return _name.replace('-','__')

    def _package_version(self, vlnv):
        """ Returns a (vln)v string """
        return "{}-{}".format(vlnv.version,
                              vlnv.revision)

    def _parse_depend(self, depends):
        """ Returns a vlnv list of dependencies """
        #FIXME: Handle conflicts
        deps = []
        _s = "{} {} {}"
        for d in depends:
            deps.append(_s.format(self._package_name(d),
                                  d.relation,
                                  self._package_version(d)))
        return ", ".join(deps)

    def add(self, core):
        """ Adds new cores to the DB. Extisting cores with the same name are overwritten """
        name = str(core.name)
        # logger.debug("Adding core " + name)
        if name in self._cores:
            _s = "Replacing {} in {} with the version found in {}"
            logger.debug(_s.format(name,
                                   self._cores[name].core_root,
                                   core.core_root))
        self._cores[name] = core

    def find(self, vlnv=None):
        """ Returns matching core if present, else returns None

        :param vlnv: 'vlnv' match candidate. When not present, returns all cores.

        """
        if vlnv:
            found = self._solve(vlnv, only_matching_vlnv=True)[-1]
        else:
            found = list(self._cores.values())
        return found

    def solve(self, top_core, flags):
        return self._solve(top_core, flags)

    def _solve(self, top_core, flags={}, only_matching_vlnv=False):
        """ Resolves dependency queries against the CoreDB



        :param vlnv top_core: Core to resolve depenedencies against.
        :param flags:
        :param only_matching_vlnv: Only returns a single core exactly matching the vlnv given

        :returns:

        """
        logger.debug(top_core)
        logger.debug(flags)
        logger.debug(only_matching_vlnv)
        def eq_vln(this, that):
            """ Checks if the (Vendor,Library,Name) of two cores match """
            return \
                this.vendor  == that.vendor and \
                this.library == that.library and \
                this.name    == that.name

        repo = Repository()
        _flags = flags.copy()
        for core in self._cores.values():
            # If only matching a single vlnv package, continue the loop for every non-matching core
            if only_matching_vlnv:
                if not eq_vln(core.name, top_core):
                    continue

            # Create the 'package_str' suitable for simplesat parsing
            package_str = "{} {}-{}".format(self._package_name(core.name),
                                            core.name.version,
                                            core.name.revision)
            # If checking for dependency matches, add any dependencies to the 'package_str' if present
            if not only_matching_vlnv:
                _flags['is_toplevel'] = (core.name == top_core)
                _depends = core.get_depends(_flags)
                if _depends:
                    _s = "; depends ( {} )"
                    package_str += _s.format(self._parse_depend(_depends))

            # Parse the 'package_str', creating PackageMetadata suitable to add to the simplesat repo
            parser = PrettyPackageStringParser(EnpkgVersion.from_string)
            package = parser.parse_to_package(package_str)
            package.core = core

            repo.add_package(package)

        # Perform the dependency check, outputting a list of cores if possible
        request = Request()
        _top_dep = "{} {} {}".format(self._package_name(top_core),
                                     top_core.relation,
                                     self._package_version(top_core))
        requirement = Requirement._from_string(_top_dep)
        request.install(requirement)
        installed_repository = Repository()
        pool = Pool([repo])
        pool.add_repository(installed_repository)
        solver = DependencySolver(pool, [repo], installed_repository)

        try:
            transaction = solver.solve(request)
        except SatisfiabilityError as e:
            raise DependencyError(top_core.name,
                                  msg=e.unsat.to_string(pool))
        except NoPackageFound as e:
            raise DependencyError(top_core.name)

        return [op.package.core for op in transaction.operations]

class CoreManager(object):
    """ Holds an instance of a CoreDB, and manages access to that database """

    def __init__(self, config):
        self.config = config
        self._cores_root = []
        self.db = CoreDB()

    def load_cores(self, path):
        """ Searches recursively for valid cores and adds them to the CoreDB() instance

        If a 'FUSESOC_IGNORE' file is found, the directory is not checked.

        """
        logger.debug("Checking for cores in " + path)
        for root, dirs, files in os.walk(path, followlinks=True):
            if 'FUSESOC_IGNORE' in files:
                del dirs[:]
                continue
            for f in files:
                if f.endswith('.core'):
                    core_file = os.path.join(root, f)
                    try:
                        core = Core(core_file, self.config.cache_root)
                        self.db.add(core)

                    except SyntaxError as e:
                        w = "Parse error. Ignoring file " + core_file + ": " + e.msg
                        logger.warning(w)
                    except ImportError as e:
                        w = 'Failed to register "{}" due to unknown provider: {}'
                        logger.warning(w.format(core_file, str(e)))

    def add_cores_root(self, path):
        """ Invokes load_cores() for a valid path argument, which has not been previously cached """
        if not path:
            return

        if os.path.isdir(os.path.expanduser(path)) == False:
            raise IOError(path + " is not a directory")

        abspath = os.path.abspath(os.path.expanduser(path))
        if abspath in self._cores_root:
            return

        self.load_cores(os.path.expanduser(path))
        self._cores_root += [abspath]

    def get_cores_root(self):
        """ Return all core_root paths registered by the CoreManager() object """
        return self._cores_root

    def get_depends(self, core, flags):
        """ Returns list of Core() objects on which the argument core depends """
        logger.debug("Calculating dependencies for {}{} with flags {}".format(core.relation,str(core), str(flags)))
        resolved_core = self.db.find(core)
        deps = self.db.solve(resolved_core.name, flags)
        logger.debug(" Resolved core to {}".format(str(resolved_core.name)))
        logger.debug(" with dependencies " + ', '.join([str(c.name) for c in deps]))
        return deps

    def get_cores(self):
        """ Returns a list of names of all cores in the CoreManager DB """
        return {str(x.name) : x for x in self.db.find()}

    def get_core(self, name):
        """ Returns a matching Core() object to the Vlnv(name) parameter if found in the CoreManager DB """
        core = self.db.find(name)
        core.name.relation = "=="
        return core

    def get_generators(self):
        generators = {}
        for core in self.db.find():
            if hasattr(core, 'get_generators'):
                _generators = core.get_generators({})
                if _generators:
                    generators[str(core.name)] = _generators
        return generators
