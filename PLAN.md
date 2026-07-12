# ontomap 마스터 플랜

> **상태 (2026-07-12)**: Phase 1~4 구현 완료 (D4 MCP 서버 포함) + 스키마 v2 + git 인사이트.
> enrichment 파일-해시 캐시의 자동 무효화는 백로그. Phase 5(릴리스)는 미착수.
> 검증: 파이프라인 테스트 16개(빌드 10 + 인사이트/diff 6) 통과, UI tsc+vite 빌드 통과,
> `ontomap serve`/`mcp`(4툴)/`impact-check`/`hotspots`/`diff` E2E 통과.
> 외부 repo 검증 1건: graphify/worked/rsl-siege-manager (221파일 풀스택) —
> `_origin` 없는 구버전 그래프에서 0파일 버그 발견·수정(+회귀 테스트).
>
> **스키마 v2**: File.symbols[](이름+줄번호), file_relationships[](파일간 엣지 139개).
> **UI**: Code Graph 탭(graphify graph.html 임베드 + ?q= 딥링크로 심볼/파일 포커스),
> 미니맵 노드색, 파일 확장자 배지, 파일스택/Related Code 펼침+파일 선택,
> 파일 상세(심볼/파일간 의존/파일단위 impact), impact 셀 클릭→Show Impact(파일도 지원),
> ⌘K "A -> B" 경로질의(BFS 하이라이트), Show Hotspots 히트맵(churn 오버레이),
> 파일 Git Activity(커밋수 + co-change + hidden coupling ⚠).
>
> **git 인사이트 (신규)**: `impact-check`(pre-commit blast radius, 논블로킹, --install-hook),
> `hotspots`(churn+co-change→hotspots.json, /hotspots.json 서빙, 숨은결합 탐지),
> `diff`(온톨로지 구조 비교 — PR 리포트 엔진). graphify repo 943커밋 검증:
> extract.py 212커밋 최다, __main__.py<->detect.py 35회 co-change에 구조엣지 없음(숨은결합) 발견.

목표: **"어떤 코드베이스든 자동 생성되는, 항상 최신인 C4 지도"** — graphify 위의 온톨로지 레이어.
기준 화면: `~/스크린샷/Ontology Map View.png` (3열: 사이드바 / 밴드형 계층 캔버스 / 상세 패널).

전략 요약: 스키마가 계약이다. 파이프라인(Python)과 UI(React)는 ontology.json 하나로만 만난다.
graphify와 같은 2단계 철학 — 키 없이 돌아가는 결정적 스켈레톤 + 호스트 에이전트(Claude) 보강.
모든 추론에는 신뢰도 태그(EXTRACTED / INFERRED-heuristic / INFERRED-llm)가 붙는다.

---

## Phase 1 — 스키마 v1 확정 + 파이프라인 보강 (ontomap/ Python)

UI가 소비할 최종 계약을 완성한다. 이 페이즈가 끝나면 ontology.json만으로 목업의 모든
데이터 요소를 채울 수 있어야 한다.

### 1.1 ontology.json 스키마 v1

```jsonc
{
  "schema_version": 1,
  "meta": {
    "built_at": "...", "source_graph": "graphify-out/graph.json",
    "graph_stats": {"nodes": 8517, "edges": 15356, "communities": 649},
    "level_counts": {"product":1, "feature":4, "component":10, "module":3,
                     "file":52, "external":9, "database":0}
  },
  "id": "product_x", "type": "Product", "name": "...",
  "description": "...",                    // NEW: CLI 플래그 or enrichment
  "children": [ /* Feature > Component > Module > File 재귀 */ ],
  // 각 노드 공통 필드:
  //   id, type, name, confidence, rationale
  //   description (NEW), responsibilities[] (NEW, Component만)
  //   stats: {files, loc, functions(NEW), dependencies(NEW)}  ← 하위 롤업
  "component_relationships": [
    // 기존: depends_on | calls | references | implements
    // NEW: integrates_with (component→external), uses(→database)
    {"source": "...", "target": "...", "relation": "...", "confidence": "...",
     "count": 3}                           // NEW: 롤업된 파일레벨 엣지 수
  ],
  "external": [...], "database": [...],
  "impact": {                              // NEW: 빌드타임 사전계산
    "component_x": {"direct": ["..."], "indirect": ["..."], "total_files": 127}
  }
}
```

v1에서 의도적으로 제외 (정직성 원칙 — 억지 추론 금지):
- `reads/writes`, `publishes/emits`, `exposes` 관계 → DB/큐/API 감지 휴리스틱이 준비되는 v2에서
- Timeline/Matrix 뷰 데이터

### 1.2 파이프라인 작업 항목

| # | 작업 | 방법 |
|---|------|------|
| P1-1 | External 엣지 롤업 | graph.json의 파일→외부패키지 import 엣지(현재 dangling)를 externals 목록과 이름 매칭 → `Component --integrates_with--> External` |
| P1-2 | 메트릭 롤업 | functions/dependencies를 File→Module→Component→Feature로 합산 |
| P1-3 | meta 블록 | level_counts, graph_stats(노드/엣지/커뮤니티 수), built_at |
| P1-4 | Impact 사전계산 | component_relationships 역방향 BFS — direct(1홉) / indirect(2홉+) / total_files |
| P1-5 | enrichment 스키마 v2 | `description`, `responsibilities[]` 필드 추가 + examples/ 갱신 |
| P1-6 | 에이전트 워크플로 문서화 | `docs/enrichment-spec.md`: symbols digest → 분류 규칙 → JSON 스키마 (graphify extraction-spec 스타일, 어느 에이전트든 실행 가능하게) |
| P1-7 | relation에 count 부여 | 같은 (src,tgt,relation) 파일레벨 엣지 개수 집계 |
| P1-8 | 테스트 확장 | 위 전부 tests/test_build.py에 케이스 추가 |

**완료 기준**: graphify 패키지 온톨로지 JSON 하나로 목업 우측 패널의 모든 필드
(설명·책임·관계+개수·메트릭 4타일·Related Code Top5·Impact 수치)를 채울 수 있다.

---

## Phase 2 — UI 파운데이션 (ontomap/ui/ React)

### 2.1 스택 (확정)
- Vite + React + TypeScript
- @xyflow/react (React Flow v12) — 커스텀 노드/엣지, 미니맵, 줌 내장
- zustand — 선택/필터/펼침 상태 (code-graph 패턴 재활용)
- CSS variables 다크 테마 (code-graph의 variables.css 구조 재활용)
- 외부 UI 라이브러리 없음 — 목업 카드가 전부 커스텀이라 불필요

### 2.2 디자인 토큰 (목업에서 추출)
레벨 색상: Product 보라(#8b5cf6계열) / Feature 마젠타 / Component 시안 /
Module 초록 / File 앰버 / External 빨강 / Database 파랑.
카드 = 어두운 배경 + 레벨색 보더 + 아이콘 + 레벨 뱃지 + 1줄 설명. 선택 시 글로우.
엣지 = 계층(owns/contains) 실선, 의존류 점선, 관계별 색.

### 2.3 작업 항목

| # | 작업 | 내용 |
|---|------|------|
| U1-1 | 프로젝트 스캐폴드 | vite react-ts, 타입은 스키마 v1에서 손으로 작성 (`types/ontology.ts`) |
| U1-2 | 데이터 로더 | `?src=` URL 파라미터 or 파일 드롭으로 ontology.json 로드 + 스키마 버전 체크 |
| U1-3 | **밴드형 레이아웃 엔진** | 레벨=Y밴드 고정, 밴드 내 X는 부모 중심 정렬 + 충돌 회피. 일반 레이아웃 라이브러리 대신 ~150줄 직접 구현 (계층이 이미 트리라 결정적) |
| U1-4 | 커스텀 노드 7종 | ProductCard / FeatureCard / ComponentCard / ModuleCard / FileStack(칩+오버플로) / ExternalCard / DatabaseCard |
| U1-5 | 커스텀 엣지 | 관계별 색/선스타일/라벨, 계층 엣지 vs 크로스 엣지 구분 |
| U1-6 | 펼침/접힘 | Feature 클릭→Component 노출→Module→File. 기본은 Component 레벨까지 |

**완료 기준**: graphify 온톨로지가 목업과 같은 밴드 구조로 렌더링되고 펼침/접힘이 동작한다.

---

## Phase 3 — 인터랙션 + 패널 (목업 완성)

| # | 작업 | 내용 |
|---|------|------|
| U2-1 | 상세 패널 Overview 탭 | description / responsibilities / 관계 롤업(색 화살표+개수+대상) / KEY METRICS 4타일 / RELATED CODE Top5 / IMPACT 블록 |
| U2-2 | Properties 탭 | confidence + rationale 노출 — **우리 차별점(정직한 감사추적)을 UI에서 전면에** |
| U2-3 | Dependencies 탭 | 노드 기준 in/out 관계 전체 목록 |
| U2-4 | Impact 탭 + Show Impact 모드 | 선택 노드의 역방향 도달셋 하이라이트, 나머지 딤 처리 |
| U2-5 | 좌측 사이드바 | ONTOLOGY LEVELS(카운트+클릭 필터) / FILTERS 체크박스 / RELATIONSHIP LEGEND / 푸터 통계 |
| U2-6 | 상단 툴바 | 레벨 범위 선택, Fit View, 전체화면, Show Impact 토글 (Group by·Layout 셀렉트는 자리만, 비활성) |
| U2-7 | 검색 (⌘K) | 이름 퍼지 매칭 → 선택+센터링 |
| U2-8 | 미니맵/줌 | React Flow 내장 + 스타일링 |

자리만 잡고 비활성 (v2 백로그): List/Matrix/Timeline 뷰, Group by 전환,
Code Graph View 탭(graphify graph.html 링크로 대체), Add Note.

**완료 기준**: 목업과 나란히 놓고 비교했을 때 구조·정보 요소가 일치한다 (스타일 디테일 제외).

---

## Phase 4 — 배포 형태 + 신선도 (개발자가 실제로 쓰는 물건으로)

| # | 작업 | 내용 |
|---|------|------|
| D1 | `ontomap serve` | UI 빌드 산출물을 Python 패키지에 번들, ontology.json과 함께 로컬 서빙 + 브라우저 오픈. **time-to-wow: `graphify . && ontomap build && ontomap serve` 3줄** |
| D2 | graphify 연동 | graphify post-commit hook / --watch 뒤에 ontomap build 자동 실행 (그래프 갱신→온톨로지 갱신). CodeSee가 죽은 이유(지도-코드 drift)를 구조적으로 회피 |
| D3 | enrichment 캐시 | 파일 해시 기반 — 변경된 파일만 재분류 요청 (graphify 캐시 철학 동일) |
| D4 | MCP 서버 | "checkout을 소유한 컴포넌트는?", "X 수정 시 영향 범위는?" — 에이전트가 아키텍처 맥락을 질의. graphify MCP와 보완 관계 |

**완료 기준**: 낯선 repo에서 명령 3줄로 지도가 뜨고, 커밋하면 지도가 따라온다.

---

## Phase 5 — 오픈소스 릴리스

| # | 작업 | 내용 |
|---|------|------|
| R1 | 독립 repo 분리 + 이름 확정 | PyPI/GitHub 이름 충돌 확인 후 결정 (ontomap은 작업명) |
| R2 | 쇼케이스 | graphify 자기 자신 + 외부 유명 repo 1~2개(FastAPI 등)의 온톨로지 데모 GIF/스크린샷 |
| R3 | 문서 | README(영문), enrichment-spec, 스키마 레퍼런스, "graphify 위에서 동작" 포지셔닝 명시 |
| R4 | CI | 파이프라인 테스트 + UI 빌드 + 타입체크 |
| R5 | 런치 | graphify 커뮤니티(companion tool 제안), Show HN, r/programming |

---

## 리스크와 대응

| 리스크 | 대응 |
|--------|------|
| 온톨로지 추론이 낯선 repo에서 이상한 결과 | Phase 4 전 외부 repo 2~3개 검증 게이트. 신뢰도 태그로 "추론임"을 항상 노출 |
| graphify 스키마 변경 (빠른 upstream) | 어댑터 한 겹(`_file_level_nodes` 등 그래프 접촉면)에 격리 + schema_version 체크 |
| 대형 repo에서 캔버스 성능 | 기본 Component 레벨까지만 렌더 + 펼침 시 lazy 마운트. File은 스택 칩이라 노드 수 폭발 없음 |
| enrichment 비용 | 심볼 다이제스트만 입력(파일 내용 X) + 해시 캐시 + 휴리스틱이 커버하는 구조는 LLM 스킵 |

## 실행 순서

Phase 1 → 2 → 3은 직렬 (스키마가 계약이므로).
Phase 1 완료 직후 외부 repo 1개 검증을 끼워 스키마 결함을 조기 발견.
Phase 4의 D1(serve)은 Phase 3와 병행 가능. D4(MCP)는 릴리스 후로 미뤄도 무방.
