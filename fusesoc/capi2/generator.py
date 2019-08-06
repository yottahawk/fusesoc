import sys
import yaml
import pathlib

import logging
logger = logging.getLogger(__name__)
# print(str(logger))
# print("generator.py level = " + str(logger.getEffectiveLevel()))

class Generator(object):
    filesets   = {}
    parameters = {}
    targets    = {}
    def __init__(self):
        with open(sys.argv[1]) as f:
            data = yaml.safe_load(f)

            self.files_root  = data.get('files_root')
            self.export_path = data.get('export_path')
            self.vlnv        = data.get('vlnv')
            self.input_files = data.get('files')
            self.config      = data.get('parameters')

            #Edalize decide core_file dir. generator creates file
            self.core_file = self.vlnv.split(':')[2]+'.core'

    def add_files(self, files, fileset='rtl', targets=['default'], file_type=''):
        """ Add files to the datastructure used to construct the output .core file

        This method is typically used to add generator output files to the
        resultant .core file.

        :param files: List of file path strings / or CAPI2 File object
        :param fileset: Name of fileset to be created
        :param targets: Name of target to add created fileset to
        :param file_type: File_type of created fileset

        :returns:

        """
        # Add the input files to a new fileset object
        if not fileset in self.filesets:
            self.filesets[fileset] = {'files' : []}
        self.filesets[fileset]['files'] = files
        self.filesets[fileset]['file_type'] = file_type

        # Add the created fileset to the input target
        for target in targets:
            if not target in self.targets:
                self.targets[target] = {'filesets' : []}
            if not fileset in self.targets[target]['filesets']:
                self.targets[target]['filesets'].append(fileset)

    def add_parameter(self, parameter, data={}, targets=['default']):
        """ Adds a single parameter to the datastructure used to construct the output .core file

        :param parameter: Parameter name to be added (k)
        :param data: Parameter value to be added (v)
        :param targets: Target which the parameter should used within

        :returns:

        """
        # Add the parameter to the core root
        self.parameters[parameter] = data

        # Add the parameter to the list of parameters used within the input target
        for target in targets:
            if not target in self.targets:
                self.targets[target] = {}
            if not 'parameters' in self.targets[target]:
                self.targets[target]['parameters'] = []
            if not parameter in self.targets[target]['parameters']:
                self.targets[target]['parameters'].append(parameter)

    def write(self):
        """ Write the .core file datastructures to the a valid CAPI2 .yaml .core file """
        with open(self.core_file,'w') as f:
            f.write('CAPI=2:\n')
            coredata = {
                'name'       : self.vlnv,
                'filesets'   : self.filesets,
                'parameters' : self.parameters,
                'targets'    : self.targets,
            }
            f.write(yaml.dump(coredata))
