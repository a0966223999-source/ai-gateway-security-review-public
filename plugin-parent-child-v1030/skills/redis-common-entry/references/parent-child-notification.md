# 總機與子 Agent 通知流程

## 使用範圍

沿已連接的原 X Redis Test、gateway_internal_v1、原OAuth帳戶與原任務權限執行。將總機視為接收事件並核對任務的父 Work，不將它冒稱已部署的全平台常駐服務。
先確認使用者授權協作與通知接續，核最新更正、停止、撤回及期限。一般聊天或載入本文件不建立Agent、訂閱或信件。
沿當次工具能力選路，不把任意獨立 Work（包括小桃）當本工作樹子 Agent，不借別人的訂閱/Source/讀權，不從同帳號推導任務權限。

## 接入與記錄

1. 沿原通知接入規則查父Work的原生self-owned/thread綁定及有效訂閱。只有明確啟用授權且確無原watch時，才可沿原subscribe_to_events為本Work建唯一必要watch；已存在就保留原完整prompt/triggers。建立UNKNOWN只查原筆，不盲重建。
2. 沿原任務記錄保存task/version/owner/父receiver、既有child agent ID、已授權next_step、結果位置、期限/撤回和原operation/event綁定。合成測試記錄只授權合成測試，不冒正式Source。
3. 發現 collaboration.list_agents、send_message、followup_task 及當次原inbox工具。對既有本工作樹子Agent，活躍時send_message；已結束但仍可接續時followup_task。工具未暴露或目標失聯標AGENT_NOT_AVAILABLE，不自行換Agent或開新派工。
4. 若平台實際提供thread_management.list_threads/send_message，另核其原supportsSendMessage/目標可傳訊；它是獨立Work通道，不是collaboration的別名。沒有該工具不拼HTTP或以子AgentID代替WorkID。
5. 子Agent可呼MCP不代表有自身durable thread訂閱。未有原生自身綁定，只用父Work接收通知再接續子Agent。

## 路徑A：事件到總機後接續子任務

收到真可信gateway.continuation.ready，核eventId=data.event_id、原mailbox；獨立inbox_list(limit20，沿cursor)精確查同原event/operation及原目標。其他Work事件不認領。
讀當代原任務，核原使用者授權、版本、期限/撤回、未完next_step、task→原child綁定，不能採用payload的動作/角色/AgentID當派工權限。
沿原持久once/CAS先保留交辦階段與精確子任務，再向原child交付已核續點。涉及外部效果須有原可驗防重；沒有就不啟動該效果。呼叫失回只查原child/原結果，不能盲重派。
核子回件task/event/operation與結果，保存同原結果位置並獨立讀回；才沿原事件契約處理transport ACK，再獨立查completed。在途或未完成不標業務完成。

## 路徑B：子完成後通知總機檢查

1. 在原授權任務中，子Agent先將成果、原task/version、operation、實際完成時間及結果狀態保存到原記錄，使用原CAS/版本守護，並獨立讀回。不能只把成果放通知內。
2. 若正式宿主已有唯一finalizer/完成事件，沿該原事件，不再直接寄第二筆。原USER明確授權的合成測試才沿gateway_inbox_send發本次唯一新合成通知；一般任務必須先有合法原完成事件契約，不能因本文件自創業務事件。
3. 通知只放定位：kind、test/task ID、operation、原結果記錄ID/版本、父receiver定位；不放憑證、私鑰或授權聲稱。原信外層sender_context可作對話來源關聯；信內自填指紋不當證明，不升格work_identity_verified。
4. 子Agent發送後結束，不連續互寄，不以collaboration訊息冒Redis事件。父Work可在等待外部成果時結束回合，無需持續wait或自通知輪詢。
5. 父接真事件後獨立查原信及結果當前版本，以原任務驗收條件檢查成果；子自稱完成不是父檢查結論。若要求獨立QA，父檢查不冒具名獨立QA。
6. 先保存父檢查結論並獨立讀回，才claim/ACK該本Work事件transport；再查completed，保存去敏終態並讀回。token只在同exec瞬時變數，不輸出/store/存檔。未知只查原筆，不換鍵/盲再claim；已completed不再ACK。
7. 缺成果、拒讀、版本衝突或撤回，保存具體未完階段，不為了驗通而重算子成果或ACK未完成工作。

## 時序與主動回報

收到本Work相關有效通知，先在原對話主動告知任務及正在檢查；完成或卡點後主動回報，不等使用者追問。
原USER已授權同步Hub時，成功即同步原Hub，附event/operation、結果位置、保存/讀回/ACK/completed實際狀態與限制；無此授權不自動向外貼文。不在跨Work回覆中透露無關任務正文。
活躍回合收通知正常處理，記EVENT_RECEIVED_WHILE_ACTIVE；10秒/15秒只是投遞預算，不是執行期限。
看到真正assistant final先結束、後進真通知時記POST_FINAL_NOTIFICATION_RUN_OBSERVED_NATIVE_TURN_UNKNOWN並保存observed_ordering。只有prompt/payload/record有marker不算final；缺native turn ID不阻塞通知流程，不捏造新turn成功。子completed狀態只能證檢查時已結束，不能推事件瞬間雙方原生idle時間。
不使用sleep、固定延遲sender或新時間排程冒時序。保留原持續watch/triggers enabled；本次完成不等於停watch，過期grant不自動延長。

## 防重與恢復

保存PREPARED、DISPATCH_RESERVED、RESULT_SAVED、REVIEW_SAVED、ACK/COMPLETED的實際階段和原event/operation。這些是原任務資料，不是第二授權庫。
重複通知先讀原結果和原once。已處理只查既有回執，不再呼子、不重算、不重寄。已保存但ACK失回只查同event；CAS衝突重讀，不覆寫更新版本。
原child在新回合不可見時標AGENT_NOT_AVAILABLE並保留成果引用；不能宣稱永遠可接續或全平台任意Work已連通。
明確撤回就停止對應任務，不影響其他合法通知；原授權有界，不新增費用、憑證、Redis、服務或外部模型執行。

## 最小新Work驗收指令

沿原X Redis Test為本Work測一筆已授權的父子Agent完成通知。先唯讀核工具、父Work自己的原watch與有效訂閱；有則沿用，確無且本指令授權才建本Work唯一事件watch，run_in_current_thread=true、gateway.continuation.ready、gateway_internal_v1，不建時間排程。工具/綁定UNKNOWN先停相依寫入。
建立一個本工作樹測試子Agent和唯一test_id/operation_key、30分鐘期限及原持久測試記錄；保存父receiver、原child綁定、非敏感輸入、結果位置、精確接收分支。合成記錄不冒Source。
核同記錄讀回並把精確新測試分支存入父原watch；child只算一個小算式，CAS保存成果並獨立讀回後發唯一合成完成通知，然後結束。父交辦後立刻assistant final並保final marker，不用sleep/排程。
收到可信事件後父獨立查同原信/結果，核本次原授權、期限及停止撤回，檢查算式，保存父檢查並讀回，再claim/ACK、查completed、保存最終狀態讀回。發送失回只查原鍵，結果保存後不重算。各項真回執全通才報本次PASS，時序另列。正式Source/Holder/Caller/業務QA ACK NOT_RUN。完成主動回報；未另授權不自動同步外部Hub。

## 已有實測與發布界限

本原Work於2026-11-14完成兩方向有界合成測試：
- 新信→父核對→followup原child→保存讀回→ACK/completed：Hub #57/6873729817。
- 子成果先保存→完成事件→活躍父檢查→保存讀回→ACK/completed：#57/6873870270。
- 真父assistant final→其後通知進場→父檢查時原child completed→ACK/completed：#57/6873946686；native turn與精確原生idle時間仍UNKNOWN。
這些是原Work歷史證據，不是其他Work的當次驗收。包更新不是全域runtime安裝，也不把既有獨立Work改成子Agent。供已連接外掛且實際載入本版的Work沿自身原接入使用；新Work真接入後才標其ENABLED/VERIFIED。
