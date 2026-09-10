#import "@preview/akatable:0.1.0"
#import "@preview/mosaic:0.0.1"
#import "@preview/splash:0.5.0"
#import "@preview/zero:0.7.0": num

#import mosaic.themes.metropolis as m

#show: m.setup.with(
  title: [제로에너지건축물 온실가스 감축 성과분석 및 \ 인증제 고도화 방안 연구 #v(5pt)],
  subtitle: [Part 1. ZEB 정책에 따른 국가 온실가스 감축 성과 분석 고도화],
  date: text(size: 16pt)[2026-09-09],
  // options
  font: ("Source Han Sans KR", "Noto Sans KR", "Source Sans 3"),
  font-mono: ("Sarasa Term K", "Consolas"),
  base-size: 16pt,
  colors: (
    (
      canvas: white,
      surface: white,
      text: luma(10%),
      muted: rgb("#667477"),
      line: rgb("#b8b1a8"),
      accent: splash.tailwind.orange-400,
      warning: rgb("#b58900"),
      error: rgb("#b0413e"),
    )
  ),
)

#show label("mosaic-title-display"): set text(size: 32pt)
#show label("mosaic-cell-header"): set block(fill: splash.tailwind.slate-600)
#show link: it => underline(it)
#show link: set text(fill: splash.tailwind.sky-800)
#set strong(delta: 200)
#show strong: set text(fill: splash.tailwind.sky-950)

#show heading.where(depth: 2): it => {
  context {
    let sections = query(heading.where(level: 1).before(here()))
    if sections.len() > 0 {
      block(below: 0.3em, text(size: 0.55em, weight: "regular", sections.last().body))
    }
  }
  it.body
}

#show figure: set text(size: 16pt)
#set list(marker: (sym.bullet, sym.bullet.stroked))

#show raw.where(block: false): box.with(
  fill: luma(250),
  stroke: luma(160) + 0.5pt,
  inset: (x: 2pt, y: 1pt),
  outset: (y: 2pt),
  radius: 2pt,
)

#let academic-table = akatable.academic-table.with(format: "elsevier")

#let two-col = grid.with(
  columns: (1fr, 1fr),
  align: horizon + center,
  column-gutter: 20pt,
)

// =============================================================================

#m.slide(layout: "title")

#m.slide(cells: (
  header: heading(level: 2, outlined: false, bookmarked: false)[Outline],
  body: outline(title: none, indent: auto, depth: 2),
))

== 분석 데이터

#{
  set text(size: 16pt)
  grid(columns: (1fr, 1fr))[
    + 표준 모델 선정
      - 2025년 데이터 이용
    + 성능 평가기준 설정
      - 2025년 데이터 이용
    + 기준 에너지절감량 분석
      + (2025년) 용도, 규모, 주체별 절감률 산정
      + (2026년) 주거·비주거, 주체별 산정
    + 물량 분석
      - #link("https://www.hub.go.kr/portal/opn/lps/idx-lgcpt-pvsn-srvc-list.do")[건축HUB 대용량 데이터] 이용
      - (2025년) 유형별 *면적* 산정
      - (2026년) 건물 *유형별 비율*만 분석
  ][
    #set enum(start: 5)

    + Baseline 및 성과분석 기준 설정
      - KEEI "장기 에너지수요전망" 활용 \
        (2000--2050년 부문별 온실가스 배출 전망 제공)
    + 시나리오 설정
    + 연차별 탄소저감 성과 분석
    + 시나리오 경제성 분석
  ]
}

= ECO2 분석

== ECO2 계산 결과

#grid(columns: (1fr, 1.5fr), align: horizon + center)[
  #academic-table(
    [등급별 태양광 설계 방법],
    columns: 2,
    inset: (x: 15pt, y: 10pt),
    header: ([등급], [PV 설계]),
    (
      ([Baseline], [(민간) 태양광 제외 \ (공공) 자립률 13%]),
      ([준ZEB5], [자립률 13%]),
      ([ZEB5], [자립률 20%]),
      ([ZEB4], [자립률 40%]),
      ([ZEB3], [자립률 60%]),
      ([ZEB2], [자립률 80%]),
      ([ZEB1], [자립률 100%]),
      ([ZEB+], [자립률 120%]),
    ).flatten(),
  )
][
  #figure(
    caption: [탄소배출량 평가 결과 (ECO2 배출계수)],
    image("/work/01.ECO2/02.analysis/trend/emission.svg"),
  )
]

= 물량 분석: 데이터 전처리

== 건축HUB 데이터 --- 건축인허가 기본개요

- "건축인허가 기본개요" 데이터 취득

#let glimpse = read("/work/03.quantity/02.data/00.건축인허가_기본개요_2026-07.glimpse.txt")
#{
  show raw: set text(size: 9.5pt)
  show regex("건축_구분_코드_명|(용적_률_산정_)?연면적|(세대|호|가구)_수|사용승인_일"): it => text(
    it,
    fill: splash.tailwind.orange-500,
    weight: "bold",
  )

  columns(2, gutter: 10pt, raw(glimpse, block: true, lang: "sh"))
}

== 전처리 방법

- *공공·민간 여부는 구분 불가*
- `건축_구분_코드_명`이 `'신축'`인 데이터 필터
- `사용승인_일` 기준 최근 5년 데이터 필터 (2021--2025)
  - 사용승인일 인식 실패: *21.6%*
- `주_용도_코드_명` 재분류 (*검토 필요*)
  #[
    #set list(spacing: 1.2em)
    #set text(size: 15pt)

    - *공동주택*: 다가구주택, 공동주택, 아파트, *다세대주택*, *기숙사*
    - *단독주택*: 단독주택
    - *상업용*: 제1종근린생활시설, 제2종근린생활시설, 기타제2종근린생활시설, 판매시설, *운수시설*, 업무시설, 숙박시설, 위락시설, 위험물저장및처리시설, 자동차관련시설, 야영장시설, 근린생활시설, 판매및영업시설
    - *교육사회용*: 문화및집회시설, 종교시설, 사찰, 기타종교시설, 의료시설, 교육연구시설, 노유자시설, 수련시설, 운동시설, 묘지관련시설, 관광휴게시설, 장례시설, 교육연구및복지시설
    - *기타*: *공중화장실*, 공장, 기타공장, 창고시설, 동물및식물관련시설, *분뇨.쓰레기처리시설*, 교정및군사시설, 방송통신시설, 발전시설, *가설건축물*, 자원순환관련시설, 교정시설, 국방·군사시설, *공공용시설*
  ]

== 전처리 방법: 규모 분류

- 값이 0으로 표시되는 데이터 구분을 위해 'A0', 'C0' 추가

#v(2em)
#two-col[
  #academic-table(
    [연면적 규모 분류],
    columns: 2,
    inset: (x: 20pt, y: 10pt),
    header: ([구분], [연면적 범위 [m²]]),
    (
      ([A0], $[-infinity, #num[1e-8]]$),
      ([A1], $[#num[1e-8], 500)$),
      ([A2], $[500, 1000)$),
      ([A3], $[1000, 3000)$),
      ([A4], $[3000, 10000)$),
      ([A5], $[10000, infinity)$),
    ).flatten(),
  )
][
  #academic-table(
    [세대수 규모 분류],
    columns: 2,
    inset: (x: 20pt, y: 10pt),
    header: ([구분], [세대수 범위]),
    (
      ([C0], $[-infinity, #num[1e-8]]$),
      ([C1], $[#num[1e-8], 300)$),
      ([C2], $[300, 500)$),
      ([C3], $[500, 1000)$),
      ([C4], $[1000, infinity)$),
    ).flatten(),
  )
]

= 물량 분석: 데이터

== 정제 데이터

- 허가일 최근 5년 총 307,092행

#v(1em)

#let glimpse = read("/work/03.quantity/02.data/01.data.glimpse.txt")
#{
  show raw: set text(size: 10pt)
  raw(glimpse, block: true, lang: "sh")
}

== 유닛 수 분포

- 세대수 0으로 기록된 공동주택 처리 필요

#figure(
  caption: [용도별 유닛 수 분포],
  image("/work/03.quantity/03.eda/01.units.symlog.svg", height: 90%),
)

== 유닛 수 분포 (0 제외)

#figure(
  caption: [용도별 유닛 수 분포],
  image("/work/03.quantity/03.eda/01.units.log.svg"),
)

== 용도별 허가 건수 및 연면적

#two-col(
  figure(
    caption: [최근 5년 용도별 신규 허가 건수],
    image("/work/03.quantity/03.eda/02.year.count.use.svg"),
  ),
  figure(
    caption: [최근 5년 용도별 신규 허가 연면적],
    image("/work/03.quantity/03.eda/02.year.gfa.use.svg"),
  ),
)

== 용도별 허가 건수 및 연면적 (원본 용도)

#two-col(
  figure(
    caption: [최근 5년 용도별 신규 허가 건수 (건축법상 용도)],
    image("/work/03.quantity/03.eda/02.year.count.raw.svg"),
  ),
  figure(
    caption: [최근 5년 용도별 신규 허가 연면적 (건축법상 용도)],
    image("/work/03.quantity/03.eda/02.year.gfa.raw.svg"),
  ),
)

== 연면적 규모 분포

#two-col(
  figure(
    caption: [최근 5년 연면적 규모별 신규 허가 건수],
    image("/work/03.quantity/03.eda/02.area.count.use.svg"),
  ),
  figure(
    caption: [최근 5년 연면적 규모별 신규 허가 연면적],
    image("/work/03.quantity/03.eda/02.area.gfa.use.svg"),
  ),
)

== 세대수 규모 분포

#two-col(
  figure(
    caption: [최근 5년 세대수 규모별 신규 허가 건수],
    image("/work/03.quantity/03.eda/02.strata.count.use.svg"),
  ),
  figure(
    caption: [최근 5년 세대수 규모별 신규 허가 연면적],
    image("/work/03.quantity/03.eda/02.strata.gfa.use.svg"),
  ),
)
