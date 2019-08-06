import logging
import os
import pathlib
import shutil
import copy
import yaml

from fusesoc.vlnv import Vlnv

logger = logging.getLogger(__name__)

class Edalizer(object):

    def __init__(self, vlnv, cm, flags, cores, cache_root, work_root, export_root=None, system_name=None):
        if os.path.exists(work_root):
            for f in os.listdir(work_root):
                if "vunit_out" in f:
                    pass
                elif os.path.isdir(os.path.join(work_root, f)):
                    shutil.rmtree(os.path.join(work_root, f))
                else:
                    os.remove(os.path.join(work_root, f))
        else:
            os.makedirs(work_root)

        logger.debug("Building EDA API")
        def merge_dict(d1, d2):
            for key, value in d2.items():
                if isinstance(value, dict):
                    d1[key] = merge_dict(d1.get(key, {}), value)
                elif isinstance(value, list):
                    d1[key] = d1.get(key, []) + value
                else:
                    d1[key] = value
            return d1

        generator_programs = {}

        first_snippets = []
        snippets       = []
        last_snippets  = []
        _flags = flags.copy()
        core_queue = cores[:]
        core_queue.reverse()

        for core in core_queue:
            cores = cm.get_depends(core.name, flags)
            logger.warning("Core : " + str(core.name))
            logger.warning("Depending cores : " + str([str(core.name) for core in cores]))

        while core_queue:
            snippet = {}
            core = core_queue.pop()
            logger.info("Preparing " + str(core.name))
            core.setup()

            logger.debug("Collecting EDA API parameters from {}".format(str(core.name)))
            _flags['is_toplevel'] = (core.name == vlnv)

            if export_root:
                # Copy .core files to their location in the build_tree (Typically 'VLNV/src/')
                files_root = os.path.join(export_root, core.sanitized_name)
                core.export(files_root, _flags)
            else:
                files_root = core.files_root

            # 'rel_root' == relative path between the files_root (exported location) and work_root (eda.yml file)
            # By default, this will be "../src/VLNV"
            # If --no-export, it could be anything, depending on where the core_root is in the filesystem. If the
            # core is remote(git/github), it will likely be in the .cache/fusesoc directory. For local cores, it
            # can be anywhere.....
            rel_root = os.path.relpath(files_root, work_root)

            #Extract parameters
            snippet['parameters'] = core.get_parameters(_flags)

            #Extract tool options
            snippet['tool_options'] = {flags['tool'] : core.get_tool_options(_flags)}

            #Extract scripts
            snippet['scripts'] = core.get_scripts(rel_root, _flags)

            # Construct list of files to place into "eda.yml" file
            _files = []
            for f in core.get_files(_flags):
                if f.copyto:
                    _name = f.copyto
                    dst = os.path.join(work_root, _name)
                    _dstdir = os.path.dirname(dst)
                    if not os.path.exists(_dstdir):
                        os.makedirs(_dstdir)
                    shutil.copy2(os.path.join(files_root, f.name),
                                 dst)
                else:
                    _name = os.path.join(rel_root, f.name)
                _files.append({
                    'name'            : _name,
                    'file_type'       : f.file_type,
                    'is_include_file' : f.is_include_file,
                    'logical_name'    : f.logical_name})
            snippet['files'] = _files

            logger.warning("get_depends ({})".format(core.name))
            cores = cm.get_depends(core.name, flags)
            logger.warning("Depending cores : " + str([str(core.name) for core in cores]))

            #Extract VPI modules
            snippet['vpi'] = []
            for _vpi in core.get_vpi(_flags):
                snippet['vpi'].append({'name'         : _vpi['name'],
                                       'src_files'    : [os.path.join(rel_root, f) for f in _vpi['src_files']],
                                       'include_dirs' : [os.path.join(rel_root, i) for i in _vpi['include_dirs']],
                                       'libs'         : _vpi['libs']})

            #Extract generator instances if allowed in CAPI, and add to list of all instances found in all cores
            if hasattr(core, 'get_generators'):
                generator_programs.update(core.get_generators(_flags))

            #Run generators
            if hasattr(core, 'get_ttptttg'):
                invoked_generator_instances = core.get_ttptttg(_flags)
                for name, gi in invoked_generator_instances.items():
                    try:
                        gi.generator = generator_programs[gi.generator]
                    except:
                        raise RuntimeError("Could not find generator program '{}' used by generator instance {}".format(gi.generator, name))

                    # If 'filesets' are specified, place all fileset files into a new list 'files', and
                    # pass that to the generator. These files should be given with an absolute path.
                    _files = []
                    if gi.filesets:
                        for fs in gi.filesets:
                            if fs not in core.filesets:
                                raise SyntaxError("Fileset {} requested by generator {} not found in filesets for core {}".format(fs, gi['gi_name'], core.name))
                            for f in core.filesets[fs].files:
                                if f.copyto:
                                    raise SyntaxError("File {} with 'copyto' attribute cannot be passed to generator. Generators do not support files with 'copyto' attributes".format(f.name))
                                _name = os.path.join(rel_root, f.name)
                                abspath = pathlib.Path(pathlib.Path(work_root) / _name).resolve().absolute()
                                abspath.exists() # Check
                                _files.append(str(abspath.as_posix()))
                    gi.fileset_files = _files

                    # Also add the export_path to the generator
                    gi.export_path = str(pathlib.Path(files_root).absolute())

                    _ttptttg = Ttptttg(name, gi, core)
                    for gen_core in _ttptttg.generate(cache_root):
                        gen_core.pos = gi.position
                        core_queue.append(gen_core)
                        # Also update the coreDB with the generated core
                        logger.warning(core.targets)
                        logger.warning(type(core.targets))
                        current_target = core._get_target(_flags)
                        for fileset in current_target.filesets:
                            if fileset.depend:
                                logger.warning(fileset.depend)
                        exit(0)
                        cm.load_cores(gen_core.core_root)

            if hasattr(core, 'pos'):
                if core.pos == 'first':
                    first_snippets.append(snippet)
                elif core.pos == 'last':
                    last_snippets.append(snippet)
                else:
                    snippets.append(snippet)
            else:
                snippets.append(snippet)

        top_core = cores[-1]
        self.edalize = {
            'version'      : '0.2.0',
            'files'        : [],
            'hooks'        : {},
            'name'         : system_name or top_core.sanitized_name,
            'parameters'   : {},
            'tool_options' : {},
            'toplevel'     : top_core.get_toplevel(flags),
            'vpi'          : [],
        }

        for snippet in first_snippets + snippets + last_snippets:
            merge_dict(self.edalize, snippet)

    def to_yaml(self, edalize_file):
        with open(edalize_file,'w') as f:
            f.write(yaml.dump(self.edalize))

from fusesoc.core import Core
from fusesoc.utils import Launcher

class Ttptttg(object):

    def __init__(self, gi_name, gi, core):
        self.gi_name = gi_name
        self.gi = gi

        vlnv_str = ':'.join([core.name.vendor,
                             core.name.library,
                             core.name.name+'-'+self.gi_name,
                             core.name.version])
        self.vlnv = Vlnv(vlnv_str)

        logger.warning(gi)
        logger.warning(type(gi))
        # 'files_root'  : location of core src_files after fetch operation (remote/local)
        # 'export_root' : if src_files are exported during config stage, the location of the export
        self.generator_input = {
            'gapi'       : '1.0',
            'vlnv'       : vlnv_str,
            'files_root' : os.path.abspath(core.files_root),
            'export_path': gi.export_path,
            'files'      : gi.fileset_files or '',
            'parameters' : dict(gi.parameters),
        }

    def generate(self, cache_root):
        """ Executes a parametrized generator instance

        Args:
            cache_root (str): The directory where to store the generated cores

        Returns:
            list: Cores created by the generator
        """
        generator_cwd = os.path.join(cache_root, 'generated', self.vlnv.sanitized_name)
        generator_input_file  = os.path.join(generator_cwd, self.gi_name+'_input.yml')

        logger.info('Generating ' + str(self.vlnv))
        if not os.path.exists(generator_cwd):
            os.makedirs(generator_cwd)
        with open(generator_input_file, 'w') as f:
            f.write(yaml.dump(self.generator_input))

        args = [os.path.join(os.path.abspath(self.gi.generator.root), self.gi.generator.command),
                generator_input_file]

        if self.gi.generator.interpreter:
            args[0:0] = [self.gi.generator.interpreter]

        Launcher(args[0], args[1:],
                 cwd=generator_cwd).run()

        cores = []
        logger.debug("Looking for generated cores in " + generator_cwd)
        for root, dirs, files in os.walk(generator_cwd):
            for f in files:
                if f.endswith('.core'):
                    try:
                        cores.append(Core(os.path.join(root, f)))
                    except SyntaxError as e:
                        w = "Failed to parse generated core file " + f + ": " + e.msg
                        raise RuntimeError(w)
        logger.debug("Found " + ', '.join(str(c.name) for c in cores))
        return cores
