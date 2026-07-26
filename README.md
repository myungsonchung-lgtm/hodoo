# toe-mcp

TouchDesigner `.toe` 파일을 **직접 읽고 쓰는** MCP(Model Context Protocol) 서버입니다.
Claude Desktop, Claude Code 등 MCP를 지원하는 클라이언트에 연결해서, AI가 내 PC의
`.toe` 파일 내용을 열어보고 수정한 뒤 다시 저장하도록 할 수 있습니다.

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
