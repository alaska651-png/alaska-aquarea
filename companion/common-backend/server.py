"""Loopback-only, authenticated GET API. Running this module is opt-in."""
import asyncio, hmac, json, os
from pathlib import Path
from acquisition import Collector
from model import Model

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ('temperatures', 'humidity', 'rooms', 'zones', 'dhw', 'buffer', 'pumps', 'energy', 'operating', 'metrics')


def create_app(model, token):
    from aiohttp import web
    if len(token) < 24:
        raise ValueError('API_TOKEN must have at least 24 characters')

    @web.middleware
    async def guard(request, handler):
        if request.method not in ('GET', 'HEAD'):
            raise web.HTTPMethodNotAllowed(request.method, ['GET', 'HEAD'])
        supplied = request.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
            raise web.HTTPUnauthorized()
        response = await handler(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'"})
        return response

    app = web.Application(middlewares=[guard], client_max_size=1024)
    async def health(request):
        data = model.snapshot()
        return web.json_response({'read_only': True, 'sources': data['sources'], 'api_version': '1'})
    async def snapshot(request):
        data = model.snapshot()
        section = request.match_info.get('section')
        if section and section not in SECTIONS:
            raise web.HTTPNotFound()
        return web.json_response(data if not section else {'api_version': '1', 'generated_at': data['generated_at'], section: data[section]})
    app.router.add_get('/healthz', health)
    app.router.add_get('/api/v1/snapshot', snapshot)
    app.router.add_get('/api/v1/{section}', snapshot)
    return app


async def main():
    from aiohttp import web
    cfg_path = os.environ.get('COMMON_CONFIG')
    cfg = json.loads(Path(cfg_path).read_text(encoding='utf-8')) if cfg_path else {}
    catalog = json.loads((ROOT / 'home-assistant/pdc-aquarea/parameters.json').read_text(encoding='utf-8'))
    model = Model(cfg, catalog)
    app = create_app(model, os.environ.get('API_TOKEN', ''))
    collector = Collector(model, cfg, os.environ)
    # Configure upstream validation before listening, without starting old backends.
    from acquisition import local_url
    for name in ('HA_URL', 'HEISHAMON_URL'):
        if os.environ.get(name):
            local_url(os.environ[name])
    task = asyncio.create_task(collector.run())
    runner = web.AppRunner(app, access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, '127.0.0.1', int(os.environ.get('API_PORT', '8768'))).start()
        await task
    finally:
        task.cancel()
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
