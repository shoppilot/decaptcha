"""Stub for implementing DeathByCaptcha service"""
import logging
from scrapy import signals
from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy.utils.misc import load_object
from twisted.internet.defer import maybeDeferred
from urlparse import urlparse

logger = logging.getLogger(__name__)


class DecaptchaMiddleware(object):

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def __init__(self, crawler):
        self.crawler = crawler
        self.settings = crawler.settings
        self.engines = self._load_objects(
            self.settings.getlist('DECAPTCHA_ENGINES')
        )
        self.solver, = self._load_objects(
            self.settings.getlist('DECAPTCHA_SOLVER')
        )[:1] or [None]
        self.enabled = self.settings.getbool('DECAPTCHA_ENABLED')
        self.domains = self.settings.getlist('DECAPTCHA_DOMAINS')
        self.paused = False
        self.queue = []
        if not self.enabled:
            raise NotConfigured('Please set DECAPTCHA_ENABLED to True')
        if not self.solver:
            raise NotConfigured('No valid DECAPTCHA_SOLVER provided')
        if not self.engines:
            raise NotConfigured('No valid DECAPTCHA_ENGINES provided')
        crawler.signals.connect(self.spider_idle,
                                signal=signals.spider_idle)

    def is_captcha_domain(self, request):
        if self.domains:
            parsed_url = urlparse(request.url)
            for d in self.domains:
                if d in parsed_url.netloc:
                    return True
            return False
        return True

    def process_request(self, request, spider):
        if request.meta.get('captcha_request', False):
            return
        if self.paused and self.is_captcha_domain(request):
            self.queue.append((request, spider))
            raise IgnoreRequest('Crawling paused, because CAPTCHA is '
                                'being solved')

    def process_response(self, request, response, spider):
        if request.meta.get('captcha_request', False):
            return response
        if self.paused and self.is_captcha_domain(request):
            self.queue.append((request, spider))
            raise IgnoreRequest('Crawling paused, because CAPTCHA is '
                                'being solved')
        # A hack to have access to .meta attribute in engines and solvers
        response.request = request
        for engine in self.engines:
            if self.is_captcha_domain(request) and engine.has_captcha(response):
                logger.info('CAPTCHA detected, getting CAPTCHA image')
                self.pause_crawling()
                # self.queue.append((request, spider))
                # engine should handle
                dfd = maybeDeferred(engine.handle_captcha,
                                    response=response, solver=self.solver)
                dfd.addCallback(self.captcha_handled)
                dfd.addErrback(self.captcha_handle_error)
                raise IgnoreRequest('Response ignored, because CAPTCHA '
                                    'was detected')
        return response

    def pause_crawling(self):
        self.paused = True

    def resume_crawling(self):
        self.paused = False
        for request, spider in self.queue:
            request.dont_filter = True
            self.crawler.engine.crawl(request, spider)
        self.queue[:] = []

    def spider_idle(self):
        self.resume_crawling()

    def captcha_handled(self, _):
        logger.info('CAPTCHA handled, resuming crawling')
        self.resume_crawling()

    def captcha_handle_error(self, failure):
        logger.info('CAPTCHA handle error: {}'.format(failure))
        self.resume_crawling()

    def _load_objects(self, classpaths):
        objs = []
        for classpath in classpaths:
            obj = load_object(classpath)(self.crawler)
            objs.append(obj)
        return objs
