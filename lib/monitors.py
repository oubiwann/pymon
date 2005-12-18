from email.MIMEText import MIMEText

import dispatch

from zope.interface import implements

from twisted.internet import reactor
from twisted.spread import pb
from twisted.web.client import HTTPClientFactory, PartialDownloadError
from twisted.internet.protocol import ClientFactory

from adytum.util.uri import Uri

from registry import globalRegistry
from application import State, History
from workflow import service as workflow
from clients import base, ping, http, ftp, smtp
import utils

class AbstractFactory(object):
    '''
    A class for generating a specific type of monitor, depending
    on the passed monitor type.

    The monitors are really client factories, and hold configuration
    and other data that the clients need or have use for.
    '''
    def __init__(self, uid):

        self.uid = uid
        self.type = utils.getTypeFromUri(uid)

    [ dispatch.generic() ]
    def makeMonitor(self):
        '''
        Generic method for client creation.
        '''

    [ makeMonitor.when("self.type == 'ping'") ]
    def makePingMonitor(self):
        monitor = PingMonitor(self.uid)
        return monitor

    [ makeMonitor.when("self.type == 'http_status'") ]
    def makeHttpStatusMonitor(self):
        monitor = HttpStatusMonitor(self.uid)
        return monitor

    [ makeMonitor.when("self.type == 'http_text'") ]
    def makeHttpTextMonitor(self):
        monitor = HttpTextMonitor(self.uid)
        return monitor

    [ makeMonitor.when("self.type == 'ftp'") ]
    def makeFtpMonitorMonitor(self):
        monitor = FtpMonitor(self.uid)
        return monitor

    [ makeMonitor.when("self.type == 'smtp_status'") ]
    def makeSmtpStatusMonitor(self):
        monitor = SmtpStatusMonitor(self.uid)
        return monitor

    [ makeMonitor.when("self.type == 'smtp_mail'") ]
    def makeSmtpMailMonitor(self):
        monitor = SmtpMailMonitor(self.uid)
        return monitor

class MonitorMixin(object):

    def __init__(self, uid):
        self.uid = uid
        self.cfg = globalRegistry.config
        self.service_type = utils.getTypeFromUri(self.uid)
        self.service = getattr(self.cfg.services, self.service_type)
        self.interval = None
        self.setInterval()
        self.workflow = workflow.ServiceState(workflow.state_wf)
        self.history = History()
        self.state = State()
        self.statedefs = self.cfg.state_definitions
        self.service_cfg = utils.getEntityFromUri(self.uid)
        self.type_defaults = self.service.defaults

    def __call__(self):
        reactor.connectTCP(*self.reactor_params)

    def setInterval(self, seconds=None):
        if seconds:
            self.interval = seconds
        elif not self.interval:
            try:
                interval = utils.getEntityFromUri(self.uid).interval
            except AttributeError:
                interval = utils.getDefaultsFromUri(self.uid).interval
            self.interval = interval

    def getInterval(self):
        return self.interval

class HttpTextMonitor(HTTPClientFactory, MonitorMixin):
    
    def __init__(self, uid):
        MonitorMixin.__init__(self, uid)
        self.page_url = ''
        self.text_check = ''
        self.checkdata = self.service.entries.entry(uri=self.uid)
        self.reactor_params = ()

class HttpStatusMonitor(HTTPClientFactory, MonitorMixin):
    
    protocol = http.HttpStatusClient

    def __init__(self, uid):
        MonitorMixin.__init__(self, uid)
        self.page_url = 'http://%s/' % self.service_cfg.uri
        # XXX write a getTimeout method
        #timeout = self.service_cfg.timeout
        #timeout = self.type_defaults.timeout
        self.host = Uri(uid).getAuthority().getHost()
        self.agent = self.cfg.user_agent_string
        self.method = 'HEAD'
        self.status = None
        # XXX write a method to get the http port from defaults or service config
        #port = self.service_cfg.http_port
        port = self.type_defaults.remote_port
        self.reactor_params = (self.host, port, self)

    def __call__(self):
        HTTPClientFactory.__init__(self, self.page_url, method=self.method, 
            agent=self.agent, timeout=int(self.type_defaults.interval))
        MonitorMixin.__call__(self)
        d = self.deferred
        d.addCallback(self.printStatus)
        d.addErrback(self.errorHandlerPartialPage)

    def printStatus(self):
        print 'Here is the return status: %s' % self.status

    def errorHandlerPartialPage(self, failure):
        failure.trap(PartialDownloadError)
        print "Hmmm... got a partial page..."
        print 'Here is the return status: %s' % self.status

    def clientConnectionFailed(self, connector, reason):
        self.message = reason.getErrorMessage()
        self.status = 'NA'
        self.protocol = base.NullClient()
        self.protocol.factory = self
        self.protocol.makeConnection()

class PingMonitor(pb.PBClientFactory, MonitorMixin):

    protocol = ping.PingClient

    def __init__(self, uid):
        pb.PBClientFactory.__init__(self)
        MonitorMixin.__init__(self, uid)

        # ping config options setup
        self.defaultcfg = self.service.defaults
        self.checkdata = self.service_cfg

        # get the info in order to make the next ping
        self.binary = self.defaultcfg.binary
        count = '-c %s' % self.defaultcfg.count
        host = Uri(self.uid).getAuthority().getHost()
        self.args = [count, host]

        #options = ['ping', '-c %s' % count, '%s' % host]
        port = int(globalRegistry.config.agents.port)
        self.reactor_params = ('127.0.0.1', port, self)

    def __call__(self):
        MonitorMixin.__call__(self)
        d = self.getRootObject()
        d.addCallback(self.pingHost)
        d.addCallback(self.getPingReturn)

    def pingHost(self, pbobject):
        return pbobject.callRemote('call', self.binary, self.args)

    def getPingReturn(self, results):
        self.data = results
        #print dir(self)
        #print results
        self.disconnect()

    def clientConnectionLost(self, connector, reason, reconnecting=1):
        """Reconnecting subclasses should call with reconnecting=1."""
        if reconnecting:
            # any pending requests will go to next connection attempt
            # so we don't fail them.
            self._broker = None
            self._root = None
        else:
            self._failAll(reason) 

class FtpMonitor(ClientFactory, MonitorMixin):

    protocol = ftp.FtpStatusClient

    def __init__(self, uid):
        MonitorMixin.__init__(self, uid)
        self.host = Uri(uid).getAuthority().getHost()
        self.port = int(self.service_cfg.port)
        self.username = self.service_cfg.username
        self.password = self.service_cfg.password
        self.passive = self.service_cfg.passive
        self.return_code = 0
        self.reactor_params = (self.host, self.port, self)

    def __call__(self):
        MonitorMixin.__call__(self)


    def clientConnectionLost(self, connector, reason):
        print "Connection Lost:", reason

    def clientConnectionFailed(self, connector, reason):
        self.return_code = 100
        print "Connection Failed:", reason.getErrorMessage()
        self.message = reason.getErrorMessage()
        self.status = 'NA'
        self.protocol = base.NullClient()
        self.protocol.factory = self
        self.protocol.makeConnection()


class SmtpStatusMonitor(ClientFactory, MonitorMixin):

    protocol = smtp.SmtpStatusClient

    def __init__(self, uid):
        MonitorMixin.__init__(self, uid)
        self.host = Uri(uid).getAuthority().getHost()
        self.port = int(self.service_cfg.port)
        self.identity = self.service_cfg.identity 
        self.status = 0
        self.reactor_params = (self.host, self.port, self)

    def buildProtocol(self, addr):
        p = self.protocol(identity=self.identity, logsize=10)
        p.factory = self
        return p

    def __call__(self):
        MonitorMixin.__call__(self)

    def clientConnectionFailed(self, connector, reason):
        print "Connection Failed: %s " % reason.getErrorMessage()
        self.message = reason.getErrorMessage()
        self.status = 'NA'
        self.protocol = base.NullClient()
        self.protocol.factory = self
        self.protocol.makeConnection()


class SmtpMailMonitor(ClientFactory, MonitorMixin):

    protocol = smtp.SmtpMailClient

    def __init__(self, uid):
        MonitorMixin.__init__(self, uid)
        self.host = Uri(uid).getAuthority().getHost()
        self.port = int(self.service_cfg.port)
        self.identity = self.service_cfg.identity
        self.status = 0
        self.mail_from = self.service_cfg.mail_from
        self.mail_to = self.service_cfg.mail_to
        self.reactor_params = (self.host, self.port, self)
    
        # Construct an email message with the appropriate headers
        msg = MIMEText("Pymon SMTP server mail check email")
        msg['Subject'] = "Pymon Test Email"
        msg['From'] = self.mail_from
        msg['To'] = self.mail_to
    
        self.mail_data = msg.as_string()

    def buildProtocol(self, addr):
        p = self.protocol(identity=self.identity, logsize=10)
        p.factory = self
        return p

    def __call__(self):
        MonitorMixin.__call__(self)

    def clientConnectionFailed(self, connector, reason):
        print "Connection Failed: %s " % reason.getErrorMessage()
        self.message = reason.getErrorMessage()
        self.status = 'NA'
        self.protocol = base.NullClient()
        self.protocol.factory = self
        self.protocol.makeConnection()