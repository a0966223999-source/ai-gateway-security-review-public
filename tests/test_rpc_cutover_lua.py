"""Execute the actual COMMIT script in system Lua with disclosed Redis fixtures.

This checks the atomic cutover predicate; it is not a live Redis integration.
No Redis server, credentials, subscriptions or network are created.
"""
import ctypes
import ctypes.util
import unittest

from transport import COMMIT


LUA_LIBRARY = ctypes.util.find_library('lua5.4')


def execute_script(created_at, cutover='100'):
    lua = ctypes.CDLL(LUA_LIBRARY)
    lua.luaL_newstate.restype = ctypes.c_void_p
    lua.luaL_openlibs.argtypes = [ctypes.c_void_p]
    lua.luaL_loadstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lua.lua_pcallk.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_longlong, ctypes.c_void_p]
    lua.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
    lua.lua_tolstring.restype = ctypes.c_void_p
    lua.lua_close.argtypes = [ctypes.c_void_p]
    # cjson and Redis commands are explicit fixtures. The predicate executed
    # below is the unmodified production Lua, not its Python translation.
    prelude = """
local writes=0
local message={event_name='gateway.continuation.ready',operation_key='op',
 expires_at=500,message='request',created_at=CREATED}
local document={messages={event=message}}
KEYS={'fixture-original-mailbox'}
ARGV={'event','op','nonce','sha','400','envelope','channel','CUTOVER'}
cjson={decode=function(raw)
 if raw=='document' then return document end
 if raw=='request' then return {kind='source-finalize-rpc-v1',nonce='nonce',requestSHA256='sha',deadline=400} end
 error('unexpected decode')
end,encode=function(value) return 'encoded' end}
redis={call=function(command,...)
 if command=='GET' then return 'document' end
 if command=='TIME' then return {'200','0'} end
 if command=='SET' or command=='PUBLISH' then writes=writes+1;return 1 end
 error('unexpected command')
end,error_reply=function(code) error(code,0) end}
local ok,result=pcall(function()
""".replace('CREATED', created_at).replace('CUTOVER', cutover)
    code = (prelude + COMMIT + "\nend)\nreturn tostring(ok)..'|'..tostring(result)..'|'..writes").encode()
    state = lua.luaL_newstate()
    if not state:
        raise RuntimeError('Lua allocation failed')
    try:
        lua.luaL_openlibs(state)
        status = lua.luaL_loadstring(state, code)
        if not status:
            status = lua.lua_pcallk(state, 0, 1, 0, 0, None)
        size = ctypes.c_size_t()
        pointer = lua.lua_tolstring(state, -1, ctypes.byref(size))
        result = ctypes.string_at(pointer, size.value).decode() if pointer else ''
        if status:
            raise RuntimeError(result)
        return result
    finally:
        lua.lua_close(state)


@unittest.skipUnless(LUA_LIBRARY, 'System Lua 5.4 is unavailable')
class AtomicCutoverTests(unittest.TestCase):
    def test_old_event_rejected_without_set_or_publish(self):
        self.assertEqual(execute_script('99'), 'false|LEGACY_EVENT_QUERY_ONLY|0')

    def test_missing_time_rejected_without_writes(self):
        self.assertEqual(execute_script('nil'), 'false|LEGACY_EVENT_QUERY_ONLY|0')

    def test_forged_future_time_rejected_without_writes(self):
        self.assertEqual(execute_script('201'), 'false|LEGACY_EVENT_QUERY_ONLY|0')

    def test_fractional_time_rejected_without_writes(self):
        self.assertEqual(execute_script('100.5'), 'false|LEGACY_EVENT_QUERY_ONLY|0')

    def test_new_event_saves_on_existing_script(self):
        self.assertEqual(execute_script('100'), 'true|saved|2')

    def test_absent_cutover_preserves_legacy_script_behavior(self):
        self.assertEqual(execute_script('nil', cutover=''), 'true|saved|2')


if __name__ == '__main__':
    unittest.main()
