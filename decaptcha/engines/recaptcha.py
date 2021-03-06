from urlparse import urljoin

import logging
import scrapy
from twisted.internet.defer import inlineCallbacks

from decaptcha.exceptions import DecaptchaError
from decaptcha.utils.download import download

logger = logging.getLogger(__name__)


class RecaptchaEngine(object):

    CAPTCHA_XPATH = '//iframe[contains(@src, "google.com/recaptcha/api")]/@src'
    CAPTCHA_FORM_XPATH = '//form[script[contains(@src, "google.com/recaptcha/api")]]'
    CAPTCHA_SITEKEY_XPATH = '//*[@id="recaptcha"]/@data-sitekey'

    def __init__(self, crawler):
        self.crawler = crawler

    def has_captcha(self, response, **kwargs):
        sel = scrapy.Selector(response)
        return len(sel.xpath(self.CAPTCHA_FORM_XPATH)) > 0 or len(sel.xpath(self.CAPTCHA_XPATH)) > 0

    @inlineCallbacks
    def handle_captcha(self, response, solver):
        sel = scrapy.Selector(response)
        form = sel.xpath(self.CAPTCHA_FORM_XPATH)
        if form:
            container = form[0]
            form_response = response
            captcha_field = 'captcha'
        else:
            iframe_src = sel.xpath(self.CAPTCHA_XPATH).extract()[0]
            iframe_url = urljoin(response.url, iframe_src)
            iframe_request = scrapy.Request(iframe_url)
            iframe_response = yield download(self.crawler, iframe_request)
            container = scrapy.Selector(iframe_response)
            form_response = iframe_response
            captcha_field = 'recaptcha_response_field'
        img_src, = container.xpath('//img/@src').extract()[:1] or [None]
        if img_src is None:
            sitekey = sel.xpath(self.CAPTCHA_SITEKEY_XPATH).extract()
            if sitekey:
                logger.info("sitekey=%s", sitekey[0])
            raise DecaptchaError('No //img/@src found on CAPTCHA page')
        img_url = urljoin(form_response.url, img_src)
        img_request = scrapy.Request(img_url)
        img_response = yield download(self.crawler, img_request)
        logger.info('CAPTCHA image downloaded, solving')
        captcha_text = yield solver.solve(img_response.body)
        logger.info('CAPTCHA solved: %s' % captcha_text)
        challenge_request = scrapy.FormRequest.from_response(
            form_response, formxpath='//form',
            formdata={captcha_field: captcha_text}
        )
        challenge_response = yield download(self.crawler, challenge_request)
        if form:
            if not challenge_response.status == 200:
                raise DecaptchaError('Bad challenge from reCAPTCHA API:\n%s' %
                                     challenge_response.body)
        else:
            challenge_sel = scrapy.Selector(challenge_response)
            challenge, = challenge_sel.xpath(
                '//textarea/text()'
            ).extract()[:1] or [None]
            if not challenge:
                raise DecaptchaError('Bad challenge from reCAPTCHA API:\n%s' %
                                     challenge_response.body)
            logger.info('CAPTCHA solved, submitting challenge')
            submit_request = scrapy.FormRequest.from_response(
                response, formxpath='//form[.%s]' % self.CAPTCHA_XPATH,
                formdata={'recaptcha_challenge_field': challenge}
            )
            submit_response = yield download(self.crawler, submit_request)
            yield download(self.crawler, response.request)
