# Router／Deer 子系統：平台共用能力

沿用 X Redis Test 原外掛、原 OAuth 帳號、原 Gateway／Holder／SQL。這是供同一帳號下已連接本外掛的其他 Work 使用的共用入口；不是對其他帳號公開，也不因讀到本頁而替所有 Work 建立訂閱、link、Source 或派工權限。

## 先選實際能力

| 使用者需求 | 原入口 | 發布／驗收範圍 |
| --- | --- | --- |
| 大任務拆小任務並保存 | batch_host_status → batch_task(goal/items) → batch_status(include_plan=true) | 已發布；由當前 Work 拆解，原 SQL 保存；普通登記不啟動 |
| 查進度及已保存結果 | batch_status；失回查 batch_receipt | 已發布；每次新快照使用新讀鍵，結果指定分頁 |
| 執行指定單項 Codex | batch_execute，模式 codex_once | 已發布且既有平台真跑／讀回通過；當次仍核宿主與原項目 |
| 一次交辦1–3個獨立子項，宿主背景逐項完成 | batch_execute_batch，或 schema 接受時 batch_task(codex_batch_once) | 已部署、15項工程測試通過；整批平台真跑驗收尚待完成，不冒稱已通過 |
| 取消未開始子項 | cancel_batch；混合批次明傳 pending_only=true | 已發布並驗證保留已完成結果；不會停止執行中程序 |
| 停止指定在途 Codex | batch_stop，或 schema 接受時 batch_task(stop_codex_once) | 已發布並驗證只停止指定受管程序與後代，保留同批其他項目 |
| Router保存／讀取續點與CAS結果 | gateway_checkpoint_create/read/save/query/receipt/link_status/revoke | 共用接口及 client 已發布；必須有本次原合法link／Source；不授予原生派工 |
| 收通知、續辦及transport ACK | 原 gateway_inbox_* 與原通知訂閱 | 有既有成功回件；每個Work仍核自己的唯一訂閱、事件、結果與lease，不能冒本人／QA ACK |
| DR固定角色定義管理 | agent_manage／agent_management_receipt | 已有受同意限制的接口；依原DR流程，定義保存與角色執行分開驗收 |
| DR固定角色執行／查回 | fixed_agent_task／fixed_agent_receipt（當次工具存在才用） | Hub已回原位安裝；runtime grant、真模型／Stop及獨立QA未驗，不列為已驗證執行能力 |
| Router自動選路、原生Work派工、依賴DAG | 尚無可宣告完整啟用的共用入口 | NOT_BOUND／NOT_DEPLOYED；不得用Codex批次或通知成功替代 |
| AIO真容器沙盒 | 另線原沙盒供應器 | NOT_DEPLOYED；Codex read-only不是容器隔離 |
| 短期記憶自動注入 | 原Reader／run factory | 程式已裝，正式Source／run能力啟用仍NOT_ENABLED；不把文件發布當runtime hook |

## 其他 Work 的最短開始方式

1. 發現當次工具，再呼叫 batch_host_status；從當次回件取得parent/generation，確認簽章心跳與lifecycle。不要複製其他Work的舊parent或10分鐘link。
2. 依使用者指示選上表入口。只有拆解／查詢時不派工；已授權執行時才呼叫明確執行工具。普通submit仍automatic_dispatch=false。
3. 由當前Work自行完成大任務拆解，不另外叫模型或建另一套Deer。用全新operation_key提交一次，獨立查回原goal／items／結果。
4. 任何寫入或執行UNKNOWN都保留精確原鍵，只查同一receipt。遇撤權、過期、帳號不符、Source不符就停止相依效果；不換帳號、重建批次或改HTTP通道。
5. 項目或結果查詢使用item_offset=0、item_limit=25。超過25項分頁；新快照用新讀鍵。
6. 本外掛已在原設定頁Refresh tools並查見batch_execute_batch／codex_batch_once。若某個舊Work仍沒有新schema，標TOOL_NOT_FOUND／BATCH_BACKGROUND_TOOL_SCHEMA_NOT_AVAILABLE；需在刷新連線後的新對話載入，不反覆重新部署或假傳未知欄位。

## 讀取具體流程

- 拆解、單項執行、取消與停止：沿SKILL.md既有章節。
- 背景批次：[有界 Codex 背景批次](codex-bounded-background.md)。
- 通知／自喚醒：[原通知規則](work-notification-selfwake.md)。
- DR角色：[DR Bot流程](dr-bot.md)。

發布這份入口說明，代表上述既有接口與用法可共用，不代表每個Work已完成自身接入或所有列項都已正式驗收。

