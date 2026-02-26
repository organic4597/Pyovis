# Escalation Follow-Up Test Scenario

## Problem Fixed
**Before**: When a task escalated and the user sent a follow-up question like "뭐가 문젠데?" (What's the problem?), the bot treated it as a new task and responded with "✅ 완료" (Complete).

**After**: The bot now recognizes follow-up questions and provides detailed escalation information.

---

## Implementation Details

### 1. Escalation State Tracking
```python
self._pending_escalations: dict[int, dict] = {}  # Track escalated tasks per chat_id
```

Each escalation stores:
- `fail_reasons`: List of error messages
- `loop_count`: Number of iterations before escalation
- `created_files`: Files generated during the loop
- `project_id`: Project directory path
- `message`: Escalation message
- `task_id`: Original task ID

### 2. Follow-Up Detection Logic
In `_handle_message()`:
```python
if chat_id in self._pending_escalations:
    # Short question with keywords → Show details
    if len(user_text) < 50 and any(word in user_text.lower() for word in 
       ["뭐", "왜", "문제", "이유", "what", "why", "problem", "reason", "어떻게", "해결"]):
        await self._send_escalation_details(chat_id, escalation)
        return
    else:
        # New substantial request → Clear state
        self._pending_escalations.pop(chat_id, None)
```

### 3. Detailed Response Format
`_send_escalation_details()` sends:
```
⚠️ *에스캼레이션 상세 정보*

{original_message}

🔴 *실패 원인:*
• Error 1
• Error 2

🔄 루프 횟수: 3

📄 *생성된 파일:*
• `file1.py`
• `file2.py`

📁 Project: `projects/task_abc123`

*해결 방법:*
• 에러를 확인하고 요청을 더 구체적으로 수정하세요
• 또는 생성된 파일을 수동으로 수정하세요
```

---

## Test Scenario

### Scenario 1: Follow-Up Question
```
User: "3d 테트리스 게임을 만들어줘"
Bot:  [Loop runs, escalates]
      ⚠️ *에스캼레이션*
      
      자동 해결 불가...
      
      🔴 *실패 원인:*
      • ImportError: pygame module not found
      • SyntaxError: invalid syntax in tetris.py line 42
      
      🔄 루프 횟수: 3
      
      📄 *생성된 파일:*
      • `tetris_game.py`
      • `game_logic.py`

User: "뭐가 문젠데?"
Bot:  ⚠️ *에스캼레이션 상세 정보*
      
      [Same detailed info as above, with 해결 방법 added]
```

### Scenario 2: New Request After Escalation
```
User: "3d 테트리스 게임을 만들어줘"
Bot:  [Escalates with errors]

User: "간단한 계산기 만들어줘"  # New substantial request (> 50 chars or no keywords)
Bot:  [Clears escalation state, processes new request normally]
```

### Scenario 3: Keywords Detected (Short Questions)
Trigger words:
- Korean: "뭐", "왜", "문제", "이유", "어떻게", "해결"
- English: "what", "why", "problem", "reason"

Examples that trigger detailed response:
- "뭐가 문젠데?"
- "왜 안돼?"
- "문제가 뭐야?"
- "What's the problem?"
- "Why did it fail?"

Examples that clear escalation state:
- "pygame 설치하고 다시 실행해줘" (> 50 chars, substantial request)
- "알았어 수동으로 고칠게" (no trigger keywords)

---

## Technical Notes

### Timeout Protection
Added 5-second timeout to prevent infinite hangs:
```python
await asyncio.wait_for(self._safe_send(chat_id, text), timeout=5.0)
```

### Debug Logging
```python
logger.info(f"[DEBUG] _send_response: status={status}, result keys={list(result.keys())}")
```

### State Cleanup
- Escalation state is stored when `status == "escalated"`
- Cleared when:
  1. User sends a new substantial request (>50 chars or no keywords)
  2. Explicitly popped from `_pending_escalations` dict

---

## Testing Checklist

- [ ] Escalation triggers and stores state correctly
- [ ] Short follow-up questions show detailed info
- [ ] New requests clear escalation state
- [ ] All trigger keywords work (Korean + English)
- [ ] Timeout prevents infinite hangs
- [ ] Debug logs appear in console
- [ ] Files and project paths display correctly
- [ ] Multiple escalations per chat_id work independently

---

## Related Commits

1. `d4c16ff` - loop_controller state machine rewrite + telegram duplicate text fix
2. `feec3fa` - Merge minimax_work: v5.2 Tool-First optimization
3. `f77c625` - fix: 에스컬레이션 후속 질문 처리 및 상세 정보 제공 (this fix)

---

## Files Modified

- `pyovis/interface/telegram_bot.py`
  - Added `_pending_escalations` dict (line 117)
  - Added `_store_escalation()` method (line 180-189)
  - Added `_send_escalation_details()` method (line 191-221)
  - Updated `_handle_message()` with follow-up detection (line 378-388)
  - Updated `_send_response()` to store escalation (line 521-522)
  - Enhanced escalation response format (line 524-541)
  - Added timeout to `send_progress()` (line 494-499)
