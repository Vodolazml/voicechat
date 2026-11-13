from starlette.exceptions import HTTPException


class BodyLimitMiddleware:
    """Caps request bodies. `overrides` lists (path regex, limit) for routes that take bigger bodies
    (chat file uploads), which then enforce their own limit."""

    def __init__(self, app, limit, overrides=()):
        self.app, self.limit, self.overrides = app, limit, tuple(overrides)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        for pattern, limit in self.overrides:
            if scope.get('method') == 'PUT' and pattern.match(scope.get('path', '')):
                return await self.app(scope, receive, send)
        size = 0
        async def bounded_receive():
            nonlocal size
            message = await receive()
            if message['type'] == 'http.request':
                size += len(message.get('body', b''))
                if size > self.limit:
                    raise HTTPException(status_code=413, detail='Request body too large')
            return message
        await self.app(scope, bounded_receive, send)
