# DR Bot 共用管理接線 R2

狀態：程式與隔離驗證完成，尚未部署或發出正式管理授權。前次宿主 Store 建立／全新程序讀回 PASS 保留為獨立證據。

## 本版行為

當前 Agent 自行設計角色，沿原 OAuth MCP 帳號和原簽章 RPC，把明確受准的具名角色定義讀／建到原 Deer AgentStore。共用的是同帳號的授權，不是把登入 cookie 分發到每個 Work。沒有 Grok API、新模型、新服務、第二名冊或 Redis。

原 Gateway 的互動 session、原 require_auth、具名 admin、threads:write／runs:create、原 thread owner 與 CSRF 通過後，才可發出獨立的管理 consent。原 Source 私有目錄保存一次性的 consent／revoked metadata，不更改 checkpoint link。最多25個明確名稱，最長3600秒，必須有read；初次實測頁固定10分鐘、唯一 qa-dr-mcp-20261112，僅list/read/create。工具、技能、委派清單全空，沒有執行能力。

本版選擇原 session 發同意→原 MCP 驗 OAuth owner→同一 signed host RPC→live user/policy 重核→原 AgentStore 的委派管理路徑。這取代前版未具備的跨程序 session transport，不轉交 cookie、不合成 AuthContext。這是新增且預設關閉的管理 gate；不是呼叫或繞過原 /api/agents HTTP gate；agents_api.enabled 不變。原 HTTP resolver 候選保留但不啟用，不同時掛兩條管理路徑。

## 原碼入口

- deer_management_consent.py：原 Source private_directory/read_at/publish/encode；immutable consent、同鍵query-only、expiry／revocation／STOP／inode／pin。
- gateway_agent_consent.py + agent_management_consent.html：原 Gateway 的管理同意／查回／撤回及中文審閱頁。按鈕前不POST；失回只查原 operation_key。頁面不索取登入憑證。
- deer_management_rpc.py：agent_manage(kind=list/read/create/receipt) 與 agent_management_receipt。同一 BatchRPC、原 principal 與 OAuth metadata；不接 owner、grant、URL、PID、模型、工具或派工欄位。create 也回原 payload SHA 供失回查帳。
- deer_management_host.py：原 host 驗 HMAC 後才可注入 ManagementDefinitionService；信任啟動選定 grant，並以原 Gateway PID/cwd/starttime、相同config/pin、原SQLite users token_version／admin／thread owner及原 policy resolver 核權。policy await之後再核expiry／revoke。所有 Store I/O 走 asyncio.to_thread 且指定真user_id。
- fresh_batch_rpc.py／fresh_batch_host.py／fresh_batch_worker.py／platform_service_manager.py：沿現有queue、control port、response簽章和worker；新增agent action，預設不在原enrollment中。原manager只轉交信任啟動選定的grant/PID，不自動選grant或重綁原程序，不另起worker。
- OriginalManagementJournal：原 Source 的持久attempt／receipt防重，create失回不重播；原Store的既有名稱不覆寫。

## 安裝與啟用的順序

1. 先以當代batch_host_status核 signed heartbeat，再從原宿主確認原Gateway/worker、當代code/pin、同批在途任務。原服務失聯、STOP、重啟競態或忙碌時，先恢復／協調，不變更pin或強停其他工作。
2. 取本PR固定commit至隔離staging，核consent-manifest；不checkout/reset live repo。Gateway需deer_agent_management.py、deer_management_journal.py、新四個Python模組與HTML。gateway-management-hook.patch僅是已知原app.py的精確錨點差額，需先比對現用原檔及AGENTS，不能整檔覆蓋。先維持 XJ_AGENT_MANAGEMENT_CONSENT_ENABLED 未設定／false。
3. 原MCP程式使用最新39a18e32ecf9183b1e1d8158448ef5d3e91db3a4的既有成果，加本版三個fresh_batch差額與新模組。原batch Stop/Execute保留，既有mcp_bridge optional HTTP adapter不啟用。不要把舊feature checkout覆蓋新部署。
4. 同意頁入口為 /api/v1/auth/agent-management-consent。只有具體啟用審閱後才打開issuer gate，沿原登入會話審閱指定role/10分鐘/零能力後由使用者批准。thread_id只是原thread定位提示；後端仍查原owner。POST不接受自報owner/grant，未登入／PAT／internal／auth-disabled均拒絕。
5. 保留原generation/parent/owner/keys，原enrollment增加agent動作時必須同時更新既有host config pin、原Gateway的DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256與原MCP的host config；不可用舊SHA混接。這不是另建host或credential。只在無在途工作且原宿主工程確認的受控部署中操作。原MCP另需 XJ_AGENT_MANAGEMENT_ENABLED=true。
6. 同一原manager只增加 --agent-management-grant-id <原session發的ID> --gateway-pid <當代原Gateway PID>，再轉交同一worker，其餘既有root/database/endpoint均沿用。原worker的current檢查任一PID/pin/STOP改變即拒絕；不要自動換PID或grant。真實grant發出前不能用測具／前次operator trial補過。
7. 以當次刷新schema的原agent_manage：create→另一次read→list→receipt。未知只用agent_management_receipt同key；需查原Store寫入則kind=receipt加原create key和SHA。再沿同意頁DELETE撤回→新read必須拒絕，保存原回件。固定Agent版本修改與停止仍NOT_BOUND，不以DELETE刪記憶補驗收。
8. 全鏈通過後才更新現有外掛目錄／共用指引，不另安裝第二個DR技能。全新Work再驗schema、原同帳號scope及讀回；不能用宿主測試冒全Agent可用。

## 回退

先以原session撤回consent，再關issuer／MCP management gate；保留原角色和所有attempt/receipt。撤回不刪角色或記憶、不停止其他Codex任務。原enrollment/pin的回復也要在受控無在途窗口成組處理，不重設權威、不清UNKNOWN、不重送寫入。前版原Gateway／MCP服務及batch功能保持其正式版本。

## 驗證與限制

62項管理／原路由／原Source／原SQLite／原CSRF／真簽章Host測試PASS，既有fresh_batch簽章邊界與Stop schema回歸17項PASS，原平台service lifecycle回歸6項PASS。共85項、exit 0。真實Source檔案與原SqlAgentStore均為隔離目錄；session credentials與policy callback是明示fixtures，不能當正式登入或獨立QA。

尚未驗證本版正式部署、真正互動consent、跨Work schema刷新、真MCP→host→registry整鏈或撤回後真拒絕；沒有執行模型。早期文件的未安裝／37項測試為歷史階段，本版以本文件與consent驗證JSON為準。Gate1、D2未改。

## 本輪現場阻塞

原MCP batch_host_status回件 worker_heartbeat_verified=false、observed_at=null。經原Codespace獨立終端唯讀核對，/proc未見app.gateway.app:app或fresh_batch_worker.py；原Source及原service root仍存在，STOP標記不存在，原supervisor紀錄的程序也已退出。這只證明當時程序未運行，未確定退出原因；未重建原啟動環境、未重置重試上限、未自動啟用管理或假報正式可用。
