# TouchDesigner 속도 진단 + 안전 최적화 (설치/포트/ MCP 불필요)
# =============================================================================
# 사용법: TouchDesigner에서 Alt+T 로 Textport 를 열고, 이 파일 내용을 통째로
#         붙여넣고 Enter. 느린 오퍼레이터 순위와 원인, 개선 제안이 바로 출력됩니다.
#
#         APPLY 를 True 로 바꾸면 "안전하고 되돌릴 수 있는" 최적화까지 적용합니다
#         (편집 중 무거운 TOP 썸네일 뷰어 끄기 — 렌더 결과에는 영향 없음).
# =============================================================================

APPLY = False   # True 로 바꾸면 안전 최적화까지 적용
TOPN = 15       # 상위 몇 개까지 볼지


def _num(v):
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def optimize():
    try:
        allops = op('/').findChildren(maxDepth=100)  # noqa: F821
    except Exception as e:
        print('operator 목록을 못 읽었습니다:', e)
        return

    rows = []
    for o in allops:
        row = {
            'op': o,
            'path': o.path,
            'type': getattr(o, 'type', ''),
            'family': getattr(o, 'family', ''),
            'cook': _num(getattr(o, 'cookTime', 0.0)),
            'cooks': int(getattr(o, 'totalCooks', 0) or 0),
        }
        try:
            if o.family == 'TOP':
                row['res'] = (int(o.width), int(o.height))
        except Exception:
            row['res'] = None
        rows.append(row)

    rows.sort(key=lambda r: r['cook'], reverse=True)

    try:
        fps = _num(getattr(project, 'cookRate', 0.0))  # noqa: F821
    except Exception:
        fps = 0.0
    budget = (1000.0 / fps) if fps else 0.0
    total = sum(r['cook'] for r in rows)

    print('=' * 64)
    print('TouchDesigner 속도 진단')
    print('  목표 fps       : %g   (프레임 예산 ~%.1f ms)' % (fps, budget))
    print('  오퍼레이터 수  : %d' % len(rows))
    print('  마지막 cook 합 : %.1f ms' % total)
    if budget and total > budget:
        print('  >>> 합계가 프레임 예산을 초과합니다. 아래 상위 노드가 원인입니다.')
    print('-' * 64)
    print('가장 느린 오퍼레이터 (마지막 cook, ms):')
    for r in rows[:TOPN]:
        res = ('  %dx%d' % r['res']) if r.get('res') else ''
        print('  %8.3f  %-42s %-5s cooks=%d%s'
              % (r['cook'], r['path'], r['family'], r['cooks'], res))

    # 큰 TOP (해상도 기준)
    big = [r for r in rows if r.get('res') and r['res'][0] * r['res'][1] > 1920 * 1080]
    big.sort(key=lambda r: r['res'][0] * r['res'][1], reverse=True)
    if big:
        print('-' * 64)
        print('해상도가 큰 TOP (메모리/GPU 부담):')
        for r in big[:TOPN]:
            print('  %dx%d  %s' % (r['res'][0], r['res'][1], r['path']))

    # 개선 제안
    print('-' * 64)
    print('개선 제안:')
    made = False
    for r in rows[:5]:
        if r.get('res') and r['res'][0] * r['res'][1] > 1920 * 1080:
            print('  - %s (%dx%d): 해상도를 낮추거나 Resolution TOP 로 축소하세요.'
                  % (r['path'], r['res'][0], r['res'][1]))
            made = True
        if r['family'] in ('DAT', 'CHOP') and budget and r['cook'] > budget * 0.25:
            print('  - %s (%s): 매 프레임 파이썬/연산이 무겁습니다. 캐싱하거나 '
                  '이벤트 기반으로 바꾸세요.' % (r['path'], r['type']))
            made = True
    if not made:
        print('  - 뚜렷한 단일 병목이 안 보이면, 느린 상태로 몇 초 둔 뒤 다시 실행하세요.')
    print('  - 편집이 느리면 Perform 모드로 전환하거나, 네트워크를 축소해서 '
          '노드 썸네일 렌더 비용을 줄이세요.')

    # 안전 최적화 (선택): 무거운 TOP 뷰어 끄기 (렌더 결과에는 영향 없음)
    print('-' * 64)
    if APPLY:
        n = 0
        for r in rows:
            o = r['op']
            try:
                if r['family'] == 'TOP' and o.viewer:
                    o.viewer = False
                    n += 1
            except Exception:
                pass
        print('안전 최적화 적용: TOP 노드 뷰어 %d개 끔 (렌더 출력 영향 없음).' % n)
        print('되돌리기: 각 노드 우클릭 → View, 또는 이 스크립트에서 o.viewer=True.')
    else:
        print('안전 최적화 미적용 (APPLY=False). 적용하려면 맨 위 APPLY=True 로 바꾸고 '
              '다시 붙여넣으세요.')
    print('=' * 64)


optimize()
