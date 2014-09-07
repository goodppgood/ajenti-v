import os
import shutil

from ajenti.api import *
from ajenti.plugins.supervisor.client import SupervisorServiceManager
from ajenti.plugins.vh.api import ApplicationGatewayComponent, SanityCheck
from ajenti.plugins.vh.processes import SupervisorRestartable
from ajenti.util import platform_select

from reconfigure.configs import SupervisorConfig
from reconfigure.items.supervisor import ProgramData


TEMPLATE_PROCESS = """
import multiprocessing

bind = 'unix:/var/run/ajenti-v-gunicorn-%(id)s.sock'
user = '%(user)s'
chdir = '%(root)s'
workers = %(workers)s or (multiprocessing.cpu_count() * 2 + 1)
"""


class GUnicornServerTest (SanityCheck):
    def __init__(self, backend):
        SanityCheck.__init__(self)
        self.backend = backend
        self.type = _('GUnicorn service')
        self.name = backend.id

    def check(self):
        s = SupervisorServiceManager.get().get_one(self.backend.id)
        if s:
            self.message = s.status
        return s and s.running


@plugin
class Gunicorn (ApplicationGatewayComponent):
    id = 'python-wsgi'
    title = 'Python WSGI'

    def init(self):
        self.config_dir = '/etc/gunicorn.ajenti.d/'
        self.checks = []

    def __generate_website(self, website):
        for location in website.locations:
            if location.backend.type == 'python-wsgi':
                location.backend.__config_name = location.backend.id.replace('-', '_') + '.py'
                c = TEMPLATE_PROCESS % {
                    'id': location.backend.id,
                    'user': location.backend.params.get('user', None) or 'www-data',
                    'root': location.path or website.root,
                    'workers': location.backend.params.get('workers', None),
                }
                open(os.path.join(self.config_dir, location.backend.__config_name), 'w').write(c)

    def create_configuration(self, config):
        self.checks = []
        if os.path.exists(self.config_dir):
            shutil.rmtree(self.config_dir)
        os.mkdir(self.config_dir)

        for website in config.websites:
            if website.enabled:
                self.__generate_website(website)

        sup = SupervisorConfig(path=platform_select(
            debian='/etc/supervisor/supervisord.conf',
            centos='/etc/supervisord.conf',
        ))
        sup.load()

        COMMENT = 'Generated by Ajenti-V'

        for p in sup.tree.programs:
            if p.comment == COMMENT:
                sup.tree.programs.remove(p)

        for website in config.websites:
            if website.enabled:
                for location in website.locations:
                    if location.backend.type == 'python-wsgi':
                        self.checks.append(GUnicornServerTest(location.backend))
                        self.__generate_website(website)
                        p = ProgramData()
                        p.name = location.backend.id
                        p.comment = COMMENT
                        p.command = 'gunicorn -c %s/%s "%s"' % (self.config_dir, location.backend.__config_name, location.backend.params['module'])
                        p.directory = location.path or website.root
                        virtualenv = location.backend.params.get('venv', None)
                        if virtualenv:
                            p.environment = 'PATH="%s:%s"' % (os.path.join(virtualenv, 'bin'), os.environ['PATH'])
                            p.command = os.path.join(virtualenv, 'bin') + '/' + p.command

                        sup.tree.programs.append(p)

        sup.save()

    def apply_configuration(self):
        SupervisorRestartable.get().schedule()

    def get_checks(self):
        return self.checks
