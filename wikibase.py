from config_private import wb_bot_user, wb_bot_pwd #, wd_user, wd_pwd
from wikibaseintegrator import wbi_login, WikibaseIntegrator
from wikibaseintegrator.datatypes.string import String
from wikibaseintegrator.datatypes.externalid import ExternalID
from wikibaseintegrator.datatypes.item import Item
from wikibaseintegrator.datatypes.monolingualtext import MonolingualText
from wikibaseintegrator.datatypes.time import Time
from wikibaseintegrator.datatypes.globecoordinate import GlobeCoordinate
from wikibaseintegrator.datatypes.url import URL
from wikibaseintegrator.models import Reference, References
from wikibaseintegrator.wbi_config import config as wbi_config
from wikibaseintegrator import wbi_helpers
from wikibaseintegrator.wbi_enums import ActionIfExists, WikibaseSnakType
from wikibaseintegrator.models.claims import Claims

user_agent = "User DavidL python script, david.lindemann@ehu.eus"
wbi = None
def wbi_setup(wikibase_name):
    global wbi
    # setup wbi for own wikibase
    wbi_config['MEDIAWIKI_API_URL'] = f'https://{wikibase_name}/w/api.php'
    wbi_config['SPARQL_ENDPOINT_URL'] = f'https://{wikibase_name}/query/sparql'
    wbi_config['WIKIBASE_URL'] = f'https://{wikibase_name}'

    login_instance = wbi_login.Login(user=wb_bot_user[wikibase_name], password=wb_bot_pwd[wikibase_name])
    wbi = WikibaseIntegrator(login=login_instance)

