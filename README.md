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

## DSI Streamer 연결 (`disconnected` / `not connected` 해결)

DSI 연결은 두 계층입니다. **어디서** disconnected가 뜨는지에 따라 고칠 대상이 다릅니다.

```
[헤드셋] ──블루투스(가상 COM 포트)──► [DSI-Streamer 앱] ──TCP 8844──► [TouchDesigner]
         └── ① dsi_connect.py ──┘                     └── ② dsi_streamer.py ──┘
```

- **① DSI-Streamer 앱 자체가 `disconnected`** (헤드셋이 앱에 안 붙음) →
  [`bridge/dsi_connect.py`](bridge/dsi_connect.py). TouchDesigner와 무관하며, 이게
  먼저 붙어야 ②가 의미 있습니다.
- **② TouchDesigner 노드가 `not connected`** (앱은 붙었는데 TD로 안 들어옴) →
  [`bridge/dsi_streamer.py`](bridge/dsi_streamer.py).

### ① 헤드셋 ↔ DSI-Streamer 앱  (`bridge/dsi_connect.py`)

DSI 헤드셋은 블루투스로 페어링되면 **가상 시리얼/COM 포트**로 잡히고, DSI-Streamer가
그 포트로 연결합니다. "계속 disconnected"의 실제 원인은 대개 **포트가 틀렸거나, 다른
프로그램이 그 포트를 잡고 있는(busy)** 경우입니다. 이 도구가 그걸 짚어줍니다.
(TouchDesigner 불필요, 표준 라이브러리로 동작하며 `pip install pyserial` 시 더 정확)

```bash
python dsi_connect.py ports          # 포트 목록 + ★DSI 후보 + 사용중(busy) 여부
python dsi_connect.py check COM4      # 특정 포트가 비었나, 다른 앱이 잡았나
python dsi_connect.py check /dev/cu.DSI7-0123
python dsi_connect.py guide           # 앱 연결 단계별 체크리스트
```

가장 흔한 함정: 포트를 다른 앱(이전 DSI-Streamer 창, TD의 Serial DAT 등)이 잡고 있음,
COM 포트를 잘못 고름(보통 2개 중 Outgoing), 헤드셋 저전압/슬립. `ports`/`check`로
바로 확인합니다. 앱에서 `connected`가 뜬 다음에 아래 ②로 넘어가세요.

**앱의 `TCP/IP` 탭이 `not connected`일 때** — 앱은 TCP 서버(기본 `127.0.0.1:8844`)로
대기 중인데 클라이언트가 아직 안 붙은 상태입니다. 순수 소켓으로 직접 접속해 서버가
살아 있는지 바로 확인합니다:

```bash
python dsi_connect.py tcp             # 서버에 접속 시도 (거부/타임아웃이면 앱 TCP 스트리밍 미시작)
python dsi_connect.py tcp --read      # 접속 + 데이터가 실제로 흐르는지 확인
python dsi_connect.py tcp --hold      # 접속 유지 → 앱 TCP 상태를 'connected'로 유지 (Ctrl-C 종료)
```

`연결됨 ✓` 이 나오면 서버는 정상 → 실제 소비자인 TouchDesigner를 붙이면 됩니다(아래 ②).
`거부됨`이면 앱에서 TCP/IP 스트리밍을 실제로 Start 했는지, 포트/주소가 맞는지 확인하세요.

### ② DSI-Streamer 앱 ↔ TouchDesigner

DSI-Streamer TCP 서버가 열려 있으면(위 `dsi_connect.py tcp` 가 `연결됨 ✓`), 이제
TouchDesigner 를 그 서버에 **클라이언트로 붙이면** 됩니다. 두 가지 방법이 있습니다.

**(A) 가장 확실 — TD 안에 붙여넣어 한 번에 연결** ([`bridge/td_dsi_setup.py`](bridge/td_dsi_setup.py))

브릿지(9981) 설치 여부와 무관하게, TouchDesigner 안에서 바로 실행되는 셋업 스크립트입니다.
`urbanbreak.space.toe` 가 열린 상태에서 `Alt`+`T` 로 Textport 를 열고 이 파일 내용을
통째로 붙여넣고 Enter (또는 Text DAT 에 붙여넣고 우클릭 → Run Script).

실행하면 DSI-Streamer 서버(`127.0.0.1:8844`)에 접속하는 **TCP/IP DAT `dsi_streamer`** 와
연결/수신 콜백(`dsi_streamer_callbacks`)이 생기고 바로 Active 됩니다. DSI-Streamer 앱의
TCP/IP 상태가 그 순간 **connected** 로 바뀝니다. 약 1초 뒤 Textport 에 수신 상태
(`connected=..., bytes=...`)가 찍혀 실제로 데이터가 들어오는지 확인됩니다.
다른 PC/포트면 파일 상단의 `HOST`/`PORT` 만 고치세요.

**(B) 터미널에서 원격 제어** ([`bridge/dsi_streamer.py`](bridge/dsi_streamer.py) — 9981 브릿지 필요)

TouchDesigner에서 DSI Streamer 피드가 **not connected** 로 뜰 때,
[`bridge/dsi_streamer.py`](bridge/dsi_streamer.py) 가 열려 있는 프로젝트에서 스트림
노드를 찾아 주소/포트를 맞추고 소켓을 다시 연결한 뒤, **실제로 데이터가 들어오는지**
까지 확인해 줍니다. (touchdesigner-mcp WebServer, 포트 9981을 사용 — 표준 라이브러리만 필요)

여기서 "DSI Streamer"는 그 피드를 TD로 들여오는 네트워크 입력 노드를 뜻합니다:
DSI-Streamer 의 TCP/IP 출력(기본 포트 **8844**)을 받는 **TCP/IP DAT**, 또는 이름에
`dsi`/`streamer` 가 들어간 DAT/CHOP. 자동으로 찾고, `--name` 으로 직접 지정할 수도 있습니다.

```bash
python dsi_streamer.py status                       # 스트림 노드 상태 (연결/데이터 여부)
python dsi_streamer.py connect                       # 자동으로 찾은 노드 재연결
python dsi_streamer.py connect --host 127.0.0.1 --port 8844
python dsi_streamer.py connect --name /project1/dsi_in
python dsi_streamer.py connect --name /project1/dsi_in --create   # 없으면 새로 생성
python dsi_streamer.py disconnect                    # 스트림 끄기 (Active off)
```

`connect` 는 주소/포트만 설정하고 노드의 `Active` 를 off→on 으로 토글하는 것이라
안전하며, `disconnect`(또는 Active 를 다시 켜기)로 되돌릴 수 있습니다.

데이터가 계속 안 들어오면 확인할 것: ① DSI-Streamer 앱에서 TCP/IP 스트리밍이 켜져
있는지(기본 포트 8844), ② 주소/포트가 맞는지(`--host`/`--port`), ③ 헤드셋이
DSI-Streamer 에 붙어 실제 신호가 나오는지.

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
