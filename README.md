# toe-mcp / toe bridge

TouchDesigner를 파이썬으로 제어하기 위한 두 가지 도구입니다. 상황에 맞게 고르세요.

| 방식 | 언제 | 실시간? | 폴더 |
| --- | --- | --- | --- |
| **① 실시간 제어 브릿지** | 지금 **열려 있는** 프로젝트를 터미널에서 즉시 조종 | ✅ | [`bridge/`](bridge/) |
| **② 파일 편집 MCP** | **저장된** `.toe` 파일 내용을 열어보고 수정 | ❌ | [`src/`](src/) |

---

# ① 실시간 제어 브릿지 (권장)

지금 TouchDesigner에서 열려 있는 프로젝트(예: `Desktop/show/urbanbreak.space.toe`)를
**터미널의 파이썬으로 실시간 조종**합니다. TouchDesigner 안에 작은 HTTP 서버
(Web Server DAT)를 띄우고, 터미널의 `toe_ctl.py`가 파이썬 코드를 보내면 TD가 그 자리에서
실행하고 결과를 돌려줍니다. 노드 추가/삭제, 파라미터 변경, 스크립트 실행이 즉시 반영됩니다.

```
터미널 (toe_ctl.py)  ──HTTP──►  TouchDesigner (Web Server DAT)  ──►  op(...) 실행
                     ◄───────                                  ◄──  결과/출력
```

## 빠른 시작

**1단계 — TouchDesigner 안에 브릿지 설치 (한 번만)**

`urbanbreak.space.toe`가 열린 상태에서:

- `Alt`+`T`로 **Textport**를 열고, [`bridge/td_setup.py`](bridge/td_setup.py) 내용을 통째로
  붙여넣고 Enter. (또는 Text DAT를 만들어 붙여넣고 노드 우클릭 → **Run Script**)

성공하면 루트에 `td_bridge_server`(포트 9980)와 `td_bridge_callbacks` 두 노드가 생기고,
Textport에 `TD bridge ready.`가 찍힙니다.

> 이 노드들을 프로젝트에 저장해두면 다음에 열 때 자동으로 다시 뜹니다.

**2단계 — 터미널에서 제어**

이 저장소의 `bridge/` 폴더에서:

```bash
# 대화형 모드 (REPL)
python toe_ctl.py

# 한 줄 실행
python toe_ctl.py "op('/').name"
python toe_ctl.py "op('/project1').par.something = 5"
```

`toe_ctl.py`는 표준 라이브러리만 쓰므로 별도 설치가 필요 없습니다.

## 자연어(프롬프트)로 실시간 제어하기 ⭐

터미널에 파이썬을 직접 치는 대신, **Claude에게 자연어로 말해서** 지금 열려 있는 TD를
실시간 제어할 수 있습니다. 이때는 아래 MCP 서버(②)가 브릿지에 명령을 전달합니다.

준비물 두 가지:

1. **TD 안에 브릿지 실행** — 위 "빠른 시작 1단계"대로 `td_setup.py`를 실행해 둡니다.
2. **Claude Desktop에 MCP 서버 등록** — 아래 "② 파일 편집 MCP" 설치 후, 설정에
   실시간 제어용 도구가 함께 들어옵니다. `claude_desktop_config.json`:

   ```jsonc
   {
     "mcpServers": {
       "toe": {
         "command": "toe-mcp",
         "env": {
           "TD_BIN": "C:\\Program Files\\Derivative\\TouchDesigner\\bin",
           "TD_BRIDGE_URL": "http://127.0.0.1:9980"
         }
       }
     }
   }
   ```

그러면 Claude에게 이렇게 말하면 됩니다:

> 지금 열려 있는 TD의 `/project1` 안에 노드 뭐가 있는지 보여줘.
> `moviefilein1`의 파일 경로를 `D:/clips/new.mov`로 바꿔줘.
> `/project1` 안에 box SOP 하나 만들어줘.

내부적으로 Claude가 이 MCP 도구를 호출합니다:

| 도구 | 하는 일 |
| --- | --- |
| `td_ping` | 열린 TD에 연결되는지 확인 |
| `td_exec` | 임의의 파이썬을 열린 TD에서 즉시 실행 (노드 생성·삭제, 파라미터 변경 등) |
| `td_ls` | 특정 op의 자식 노드 목록 |
| `td_pars` | 특정 op의 파라미터와 현재 값 |
| `td_perf` | 느린 오퍼레이터를 cook time 순으로 분석 (성능 진단) |

> 즉, "지금 TD를 프롬프트로 제어" = **브릿지(TD 안) + MCP 서버(Claude 쪽)** 둘 다
> 준비되면 됩니다. 브릿지만 있으면 터미널 `toe_ctl.py`로, 여기에 MCP까지 붙이면
> 자연어로 제어할 수 있습니다.

## REPL 예시

```
$ python toe_ctl.py
toe_ctl -> http://127.0.0.1:9980  [connected]
Type Python to run it in TouchDesigner. .help for commands, Ctrl-D to quit.
td> .ls /                         # 최상위 노드 목록
['/perform', '/project1', '/local', ...]
td> .ls /project1                 # project1 안의 노드들
td> .pars /project1/moviefilein1  # 파라미터와 현재 값
{'file': 'clip.mov', 'play': 1, ...}
td> op('/project1/moviefilein1').par.file = 'D:/clips/new.mov'   # 값 변경 (즉시 반영)
td> op('/project1').create(boxSOP, 'newbox')                     # 노드 생성
td> for o in op('/project1').children:      # 여러 줄 입력: 빈 줄로 끝냄
...     print(o.name, o.type)
...
```

멀티라인은 `:`로 끝나는 줄을 입력하면 자동으로 블록 모드가 되고, 빈 줄을 넣으면 실행됩니다.

## REPL 명령어

| 명령 | 설명 |
| --- | --- |
| `.ping` | 연결 확인 |
| `.ls [경로]` | 해당 op의 자식 노드 목록 (기본 `/`) |
| `.pars <경로>` | op의 파라미터와 현재 값 |
| `.perf [N]` | **느린 오퍼레이터 상위 N개**를 cook time 순으로 (기본 20) |
| `.file <경로>` | 로컬 `.py` 파일을 TD 안에서 실행 |
| `.help` | 도움말 |
| `.quit` / `Ctrl-D` | 종료 |

## 속도(성능) 개선

프로젝트가 느릴 때는 **"어디가 느린지 먼저 측정"** 하세요. 프로파일러가 모든
오퍼레이터의 마지막 cook time을 읽어 느린 순으로 보여줍니다.

- 터미널: `td> .perf` (또는 `.perf 30`)
- Claude 프롬프트: *"지금 TD가 왜 느린지 분석해줘"* → `td_perf` 도구 호출

```
$ python toe_ctl.py
td> .perf
fps=60  ops=842  last-cook total=23.1ms      # 60fps 예산은 ~16.7ms/프레임
slowest (ms):
    12.500  /project1/blur1        TOP   cooks=9000 3840x2160   # ← 범인
     6.200  /project1/script1      DAT   cooks=9000
```

> **먼저 느린 상태로 몇 초 돌린 뒤** 측정하세요. cook time은 "마지막 cook" 값이라,
> 느린 순간에 측정해야 병목이 제대로 잡힙니다.

측정 결과를 저에게 알려주시면 그 지점만 골라 고쳐 드립니다. TouchDesigner에서 흔한
속도 병목과 대응:

| 증상 | 원인 | 대응 |
| --- | --- | --- |
| 특정 TOP이 느림 | 해상도가 큼 (4K 등) | 해상도 낮추기, 필요한 곳만 고해상도, Resolution TOP로 축소 |
| 매 프레임 cook | 안 바뀌는데 계속 cook | 해당 op의 **Cook Type = Selective**, 안 쓰는 노드는 연결 끊기 |
| DAT/CHOP가 느림 | 매 프레임 파이썬 실행 | 이벤트 기반으로 바꾸기, 결과 캐싱, `me.time` 의존 줄이기 |
| 전체적으로 무거움 | 안 보이는 노드도 cook | 안 쓰는 네트워크 **Bypass**, 프리뷰 끄기, `project.cookRate` 확인 |
| GPU 메모리 부족 | TOP 해상도·개수 과다 | 불필요한 TOP 정리, 8-bit로 낮추기, Null로 캐시 |

이 표는 일반 원칙이고, 실제 수정은 `.perf` 결과를 보고 **당신 프로젝트에 맞게**
`td_exec`로 바로 적용할 수 있습니다 (예: 특정 TOP 해상도 절반으로, Cook Type 변경 등).

### touchdesigner-mcp (포트 9981)로 자동 최적화

[`touchdesigner-mcp`](https://github.com/8beeeaaat/touchdesigner-mcp)의 `.tox`를
쓰는 경우, 포트는 **9981**입니다. 이 저장소의 [`bridge/td9981.py`](bridge/td9981.py)로
진단부터 **안전·되돌리기 가능한 자동 최적화**까지 한 번에 할 수 있습니다.

```bash
python bridge/td9981.py perf                 # 진단만 (느린 노드 순위)
python bridge/td9981.py optimize             # 진단 + 어떤 최적화를 할지 미리보기
python bridge/td9981.py optimize --apply     # 안전 최적화 적용
python bridge/td9981.py optimize --apply --cap 1280x720   # 해상도 상한 지정
python bridge/td9981.py undo                 # 위에서 바꾼 것 전부 원복
```

`--apply`가 적용하는 것(둘 다 `undo`로 정확히 복구):

- **TOP 노드 뷰어 끄기** — 편집기 썸네일 렌더 비용만 줄이고, 송출/렌더 결과에는 영향 없음.
- **과대 TOP 해상도 축소** — 상한(기본 `1920x1080`)을 넘는 TOP만, 그것도 **자체 해상도
  (custom/fixed) 노드만** 비율을 유지해 축소합니다. 입력에서 크기를 받는 TOP은 네트워크
  로직이 깨지지 않도록 **건드리지 않고** 수동 확인 목록으로만 보여줍니다.

> 되돌리기 정보는 프로젝트 루트에 `td9981_backup`으로 저장되므로, `--apply` 후 세션이
> 이어지는 동안 `undo`로 원래 값까지 정확히 복구됩니다.

## 옵션

```bash
python toe_ctl.py --port 9980            # 포트 지정 (기본 9980)
python toe_ctl.py --url http://192.168.0.10:9980   # 다른 PC의 TD 제어
python toe_ctl.py --file scene_setup.py  # 로컬 .py를 TD에서 실행하고 종료
python toe_ctl.py --token 비밀값          # 브릿지에 토큰을 설정한 경우
```

## 보안 주의

이 브릿지는 받은 파이썬 코드를 TouchDesigner 안에서 **그대로 실행**합니다. 기본값은
로컬 접속(`127.0.0.1`)만 쓰는 것을 전제로 합니다. 공유 네트워크에서 쓰거나 다른 PC에서
접속해야 한다면, `bridge/td_setup.py`의 `CALLBACKS_SRC` 안 `TOKEN = ''`에 비밀값을 넣고
(다시 Run), 클라이언트에서 `--token 비밀값`(또는 환경변수 `TD_BRIDGE_TOKEN`)으로 맞춰
주세요.

---

# ② 파일 편집 MCP

TouchDesigner `.toe` 파일을 **직접 읽고 쓰는** MCP(Model Context Protocol) 서버입니다.
Claude Desktop, Claude Code 등 MCP를 지원하는 클라이언트에 연결해서, AI가 내 PC의
`.toe` 파일 내용을 열어보고 수정한 뒤 다시 저장하도록 할 수 있습니다.
(실시간이 아니라, 저장된 파일을 대상으로 하며 TD에서 파일을 닫고/다시 열어야 반영됩니다.)

## 어떻게 동작하나요?

`.toe`는 바이너리 파일이라 텍스트 편집기로 열 수 없습니다. 그래서 TouchDesigner에
기본 포함된 공식 유틸리티 **`toeexpand`** / **`toecollapse`** 를 이용합니다.

```
.toe  ──(expand)──►  ASCII 텍스트 트리  ──(편집)──►  ──(collapse)──►  .toe
```

- `toeexpand` : `.toe`를 사람이 읽을 수 있는 텍스트 파일 묶음(`<파일>.toe.dir/`)으로 펼침
- 텍스트 파일에서 노드/파라미터/DAT 내용 등을 검색·수정
- `toecollapse` : 수정한 텍스트를 다시 `.toe` 바이너리로 합침

> 이 방식은 Derivative가 버전 관리(Git) 워크플로용으로 제공하는 공식 방법입니다.

## 사전 준비물

1. **TouchDesigner 설치** — `toeexpand`/`toecollapse`가 설치 폴더의 `bin`에 들어 있습니다.
   - Windows: `C:\Program Files\Derivative\TouchDesigner\bin`
   - macOS: `/Applications/TouchDesigner.app/Contents/MacOS`
2. **Python 3.10 이상**

## 설치

```bash
# 저장소를 받은 폴더에서
pip install .
```

설치하면 `toe-mcp` 명령이 생깁니다. (또는 설치 없이 `python -m toe_mcp.server` 로도 실행 가능)

## Claude Desktop에 등록하기

Claude Desktop 설정 파일을 엽니다.

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

아래처럼 `mcpServers`에 추가합니다.

```jsonc
{
  "mcpServers": {
    "toe": {
      "command": "toe-mcp",
      "env": {
        // 자동 탐지가 안 될 때만 TouchDesigner의 bin 폴더를 지정하세요.
        "TD_BIN": "C:\\Program Files\\Derivative\\TouchDesigner\\bin"
      }
    }
  }
}
```

`toe-mcp` 명령이 PATH에 없다면 전체 경로나 파이썬 실행 방식으로 지정할 수 있습니다.

```jsonc
{
  "mcpServers": {
    "toe": {
      "command": "python",
      "args": ["-m", "toe_mcp.server"],
      "env": { "TD_BIN": "C:\\Program Files\\Derivative\\TouchDesigner\\bin" }
    }
  }
}
```

Claude Desktop을 재시작하면 도구가 나타납니다.

## Claude Code에 등록하기

```bash
claude mcp add toe -- toe-mcp
# TouchDesigner 경로 지정이 필요하면:
claude mcp add toe --env TD_BIN="C:\Program Files\Derivative\TouchDesigner\bin" -- toe-mcp
```

## 제공하는 도구(tools)

| 도구 | 설명 |
| --- | --- |
| `check_environment` | toeexpand/toecollapse가 잡히는지, 탐색 경로 확인 (문제 생기면 먼저 실행) |
| `expand_toe` | `.toe`를 텍스트 트리로 펼침 (편집·검색 전에 필수) |
| `list_expanded` | 펼쳐진 텍스트 파일 목록 보기 |
| `read_toe_text` | 특정 텍스트 파일 내용 읽기 |
| `write_toe_text` | 특정 텍스트 파일 통째로 덮어쓰기 |
| `edit_toe_text` | 특정 파일 안에서 문자열 찾아 바꾸기 |
| `search_toe` | 펼쳐진 전체에서 텍스트 검색 |
| `collapse_toe` | 수정한 텍스트를 다시 `.toe`로 저장 (기본으로 `.toe.bak` 백업) |

## 일반적인 사용 흐름

Claude에게 이렇게 말하면 됩니다:

> `D:\projects\show.toe` 파일 펼쳐서 "text1" DAT 내용 보여줘. 거기 문구를
> "Hello"로 바꾸고 다시 저장해줘.

내부적으로는 `expand_toe → search_toe → read_toe_text → edit_toe_text → collapse_toe`
순서로 실행됩니다.

## TouchDesigner 경로를 못 찾을 때

`check_environment`를 실행해 어디를 탐색했는지 확인하고, 아래 중 하나를 설정하세요.

- `TD_BIN` : TouchDesigner `bin` 폴더 경로
- `TOEEXPAND` / `TOECOLLAPSE` : 각 실행 파일의 전체 경로

## 주의사항

- `collapse_toe`는 원본 `.toe`를 덮어씁니다. 기본으로 `<파일>.toe.bak` 백업을 남기지만,
  중요한 프로젝트는 별도 백업을 권장합니다.
- 편집한 `.toe`를 열 때는 원본과 **같은 버전의 TouchDesigner**로 여는 것이 안전합니다.
- 이 서버는 로컬 파일에만 접근하며, 서버가 실행되는 PC의 파일 시스템을 사용합니다.

## 라이선스

MIT
