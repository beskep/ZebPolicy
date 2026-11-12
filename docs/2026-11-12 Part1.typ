#import "@preview/booktyps:0.1.1": booktabs
#import "@preview/gentle-clues:1.3.1": *
#import "@preview/fletcher:0.5.8" as fletcher: diagram, edge, node

#let conf(
  body,
  font: ("Source Han Sans KR", "Noto Sans KR"),
  font-size: 10pt,
  pseudo_italic: false,
) = {
  set page(paper: "a4", margin: 2cm, numbering: "1/1")
  set par(justify: true)
  set text(font: font, size: font-size)

  show title: set text(size: 1.6 * font-size)

  set heading(numbering: "1.", supplement: sym.section)
  show heading: set block(above: 1.2em, below: 0.8em)
  show heading: set text(size: font-size)
  show heading.where(level: 1): set text(size: 1.2 * font-size)
  show heading.where(level: 2): set text(size: 1.1 * font-size)

  set math.equation(numbering: "(1)", supplement: "Eq.")
  show math.equation: set text(font: ("New Computer Modern Math", ..font))

  show raw: set text(font: (
    "IosevkaCustomTerm",
    "Sarasa Term K",
    "AdwaitaMono Nerd Font",
    "Consolas",
    ..font,
  ))
  show raw: box.with(
    fill: luma(250),
    stroke: luma(200) + 0.5pt,
    inset: (x: 2pt, y: 1pt),
    outset: (y: 2pt),
    radius: 2pt,
  )

  show figure: set block(above: 2em, below: 2em)
  show figure.where(kind: table): set figure.caption(position: top)
  show figure.where(kind: table): set align(start)
  show figure.caption: it => par(first-line-indent: 0em)[
    *#it.supplement #context it.counter.display(it.numbering).*
    #it.body
  ]

  show table: booktabs
  show table: set par(justify: false)

  set list(marker: (sym.bullet, sym.bullet.stroked, sym.bullet.hyph))

  show emph: it => {
    show regex("[\p{Hangul}\p{Han}\p{Hiragana}\p{Katakana}]"): it => {
      if (pseudo_italic) {
        box(skew(it, ax: -12deg, reflow: false), width: 0.9em)
      } else {
        it
      }
    }
    it
  }

  body
}

#show: conf

// =============================================================================

#set par(first-line-indent: (amount: 1em, all: true))
#show figure: set text(size: 9pt)
#show ref: set text(fill: blue.desaturate(50%).darken(25%))


#set document(title: "ZEB 의무화 시나리오별 온실가스 감축량 산정 방법")
#title()

#align(right)[2026-11-12]

= 개요

본 문서는 ZEB 인증 의무화와 기존 건물 전전화(all-electric) 전환 정책의 시나리오별 건물부문 온실가스 배출 변화량 산정 방법을 정리한다.
2026-11-04 판에서 채택한 방안만 남기고, 비교안과 해석용 변수는 생략했다.

#info[
  *부호 규칙.*
  변화율($R$로 시작하는 기호)과 배출 변화량($Delta C$)은 증감 방향을 부호로 나타낸다.
  감소는 음수, 증가는 양수이다.
  감축 효과를 양수로 보일 때는 $-Delta C$를 "감축량"으로 표기한다.
]

== 결정 사항 <section:decisions>

#figure(
  table(
    columns: (auto, 1fr, auto),
    align: (left, left, left),
    table.header([항목], [채택 방안], [반영]),
    [연도 보간], [KEEI 절점(2024, 2030, 2040, 2050)을 PCHIP으로 보간], [@section:interp],
    [배출계수],
    [ECO2 고정 배출계수.
      국가 배출계수 전망은 민감도 분석에만 사용],
    [@section:ef],

    [신축 물량], [유형별 최근 10개년 평균 신축 연면적 × KEEI 재고 지수], [@section:new],
    [비주거 재고 지수], [KEEI 서비스부문 에너지 수요(5개 에너지원 합) 비례], [@section:stock],
    [계산 단위],
    [에너지원별로 계산한 뒤 배출 범위(직접·간접·신재생 상쇄)로 합산.
      \ 재고 연면적의 수준은 쓰지 않음],
    [@section:basic],

    [전력·태양광 변화율], [기준건물 순 전력(소요량 + 발전량)을 공통 분모로 사용], [@section:rate],
    [REF의 정책 반영],
    [REF는 현행 ZEB 로드맵(기본 시나리오)을 포함하는 것으로 봄.
      \ 강화·전전화 시나리오는 기본 시나리오와의 *차이*로 산정],
    [@section:ref],

    [확인 중], [공공·민간을 구분한 신축 물량 자료, KEEI 연도별 원자료, REF에 반영된 ZEB·히트펌프 규모], [—],
  ),
  caption: [결정 사항],
) <table:decisions>

= 계산 절차 <section:procedure>

== 입력, 출력과 기본 발상 <section:concept>

*출력*은 정책 시나리오별·연도별(2025--2050) 건물부문 배출량과, 기본 시나리오(= KEEI REF) 대비 배출 변화량이다.
변화량은 부문(주거·비주거), 에너지원, 배출 범위(직접·간접·신재생 상쇄)별로 나누어 제시한다.

*입력*은 네 묶음이다.

+ *KEEI 『2025 장기 에너지전망』 REF*: 부문·에너지원별 에너지 수요, 주택 수와 평균 주거면적
+ *건축 통계*: 건물 유형(용도·규모·공공/민간)별 최근 10개년 평균 신축 연면적
+ *건물 에너지 시뮬레이션(ECO2)*: 유형별 기준건물 원단위, ZEB 등급별 변화율, 전전화 전후 원단위
+ *시나리오와 공통 계수*: 허가연도별 의무 등급, 연간 전환율, 배출계수, 성능 저하율, 허가→준공 시차

기본 발상은 다음과 같다.

- 배출량의 *수준*은 KEEI 전망이 정한다.
  기준 배출량은 KEEI 에너지 수요에 배출계수를 곱한 값이다.
- 배출 변화량의 *크기*는 "정책의 영향을 받는 건물 면적 × 그 건물의 단위면적당 사용량 × 변화율"로 정해진다.
  - 면적: 신축은 10개년 평균 신축 물량을 KEEI 재고의 증가 속도에 맞춰 매년 누적한다.
    전전화는 시나리오의 연간 전환율로 정한다.
  - 사용량과 변화율: 유형별 시뮬레이션 결과를 쓴다.
    의무 등급이 높을수록 변화율이 크고, 준공 후 시간이 지나면 성능 저하로 효과가 줄어든다.
- KEEI 전망은 변화량의 크기에 신축 물량의 증감 속도로만 영향을 준다.
  그 밖에는 결과를 KEEI 배출량 대비 비율로 해석하는 기준이 된다.
- REF에는 현행 ZEB 로드맵(기본 시나리오)이 이미 반영되어 있으므로, 정책 효과는 기본 시나리오와의 *차이*로 계산한다.

== 절차

@fig:procedure 는 계산 절차 전체를 한 장으로 나타낸 것이다.
각 상자의 작은 글씨는 해당 단계의 입력 자료이고, 세부 단계는 @table:procedure 와 같다.

#figure(
  {
    // 단계 상자: 제목, 식, 입력 자료
    let step(title, ..body, src: none) = {
      set par(justify: false, first-line-indent: 0em)
      set align(center)
      strong(title)
      for b in body.pos() {
        linebreak()
        b
      }
      if src != none {
        linebreak()
        text(size: 6.5pt, fill: luma(90))[자료: #src]
      }
    }
    let c-common = (fill: white, stroke: 0.6pt + luma(60))
    let c-new = (fill: rgb("#e8f4ea"), stroke: 0.6pt + rgb("#3f8f55"))
    let c-conv = (fill: rgb("#fdf0e3"), stroke: 0.6pt + rgb("#c07a2c"))
    let c-res = (fill: rgb("#f1ecfa"), stroke: 0.6pt + rgb("#6a4fa3"))
    let c-chk = (fill: rgb("#fff8d6"), stroke: 0.6pt + rgb("#b39b20"))
    let group(..pts, color) = node(
      enclose: pts.pos(),
      fill: color.lighten(92%),
      stroke: (paint: color, thickness: 0.5pt, dash: "dashed"),
      inset: 6pt,
      corner-radius: 5pt,
      layer: -1,
      snap: false,
    )
    let gtitle(pos, body, color) = node(pos, text(size: 7.5pt, weight: "bold", fill: color, body), stroke: none)

    set text(size: 7.5pt)
    diagram(
      spacing: (7mm, 4.5mm),
      node-corner-radius: 3pt,
      node-inset: 4pt,
      edge-stroke: 0.6pt + luma(70),

      // ① 공통: 기준 배출량과 재고 지수
      gtitle((0, 1), [① 공통: KEEI 전망], luma(40)),
      node(
        (1, 1),
        step([연도 보간 (PCHIP)], [2024--2050 연도별 $Q(t, s, f)$], src: [KEEI `A1.6`, `A1.7`]),
        name: <c1>,
        width: 46mm,
        ..c-common,
      ),
      node(
        (1, 2),
        step([에너지원 대응], [석유 → LPG·등유 분할 ($r_"oil"$)], src: [에너지수급통계]),
        name: <c2>,
        width: 46mm,
        ..c-common,
      ),
      node(
        (1, 3),
        step(
          [기준 배출량 · 재고 지수],
          [$C_"REF" = Q dot "EF"$, #h(0.3em) $I_"stock"$, #h(0.3em) $n_"new"$],
          src: [ECO2 배출계수, KEEI 수요·주택],
        ),
        name: <c3>,
        width: 46mm,
        ..c-common,
      ),
      edge(<c1>, <c2>, "-|>"),
      edge(<c2>, <c3>, "-|>"),

      // ② 신축 ZEB 의무화
      gtitle((0, 4), [② 신축 ZEB 의무화], rgb("#2d6a3e")),
      node(
        (0, 5),
        step([신축 연면적], [10개년 평균 $A_"new" (b)$], src: [건축 통계 2015--2024]),
        name: <n1>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 6),
        step([수요 비중], [$q_"new" = A_"new" dot e_"base" \/ Q$], src: [ECO2 신축 기준건물]),
        name: <n2>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 7),
        step([등급 분포], [$r^p_"grade" (t_c, b, g)$], src: [신축 시나리오]),
        name: <n3>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 8),
        step(
          [평균 변화율 $R^p_"avg" (t, b, f)$],
          [$R_"grade" times r^p_"grade" times (1 + R_"deg")^(t - t_c)$],
          src: [ECO2 등급별 결과],
        ),
        name: <n4>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 9),
        step([$Delta C_"new" = C_"REF" dot n_"new" dot q_"new" dot R_"avg"$], [태양광 상쇄 $Delta C_"PV"$는 별도]),
        name: <n5>,
        width: 46mm,
        fill: c-new.fill,
        stroke: 1.2pt + rgb("#3f8f55"),
      ),
      edge(<n1>, <n2>, "-|>"),
      edge(<n2>, <n3>, "-|>"),
      edge(<n3>, <n4>, "-|>"),
      edge(<n4>, <n5>, "-|>"),

      // ③ 기존 건물 전전화
      gtitle((2, 4), [③ 기존 건물 전전화], rgb("#8a5418")),
      node(
        (2, 5),
        step([전환 대상 재고], [$A_"elig" (2024, b)$, 현 난방 연료별], src: [건축물대장, 에너지 사용량]),
        name: <x1>,
        width: 46mm,
        ..c-conv,
      ),
      node(
        (2, 6),
        step([전환 면적], [$A^p_"conv" = r^p_"conv/yr" dot A_"elig"$], src: [전전화 시나리오 X0--X3]),
        name: <x2>,
        width: 46mm,
        ..c-conv,
      ),
      node(
        (2, 7),
        step([누적 전환 면적], [$A^p_"conv,cum"$ (멸실 반영)]),
        name: <x3>,
        width: 46mm,
        ..c-conv,
      ),
      node(
        (2, 8),
        step(
          [수요 변화 비중],
          [$q_"conv" = (e_"post" - e_"pre") \/ Q$],
          [연료 $< 0$, 전력 $> 0$],
          src: [ECO2 전환 전·후],
        ),
        name: <x4>,
        width: 46mm,
        ..c-conv,
      ),
      node(
        (2, 9),
        step([$Delta C_"conv" = C_"REF" dot A_"conv,cum" dot q_"conv"$]),
        name: <x5>,
        width: 46mm,
        fill: c-conv.fill,
        stroke: 1.2pt + rgb("#c07a2c"),
      ),
      edge(<x1>, <x2>, "-|>"),
      edge(<x2>, <x3>, "-|>"),
      edge(<x3>, <x4>, "-|>"),
      edge(<x4>, <x5>, "-|>"),

      // 공통 → 분기 (가운데 열을 따라 내려감)
      edge(<c3>, (1, 6), <n2>, "--|>", label: $Q$, label-side: right),
      edge(<c3>, (1, 8), <x4>, "--|>", label: $Q$, label-side: left),
      edge(<c3>, (1, 9), <n5>, "-|>", label: $C_"REF", n_"new"$, label-side: right),
      edge(<c3>, (1, 9), <x5>, "-|>", label: $C_"REF"$, label-side: left),

      // ④ 결합 · 점검 · 결과
      gtitle((0, 11), [④ 결합 · 점검 · 결과], rgb("#4b3580")),
      node(
        (1, 11),
        step([시나리오 배출 변화량], [$Delta C^p = Delta C^p_"new" + Delta C^p_"conv"$]),
        name: <r1>,
        width: 46mm,
        ..c-res,
      ),
      node(
        (1, 12),
        step([REF = 기본 시나리오], [$C^p = C_"REF" + (Delta C^p - Delta C^"기본")$]),
        name: <r2>,
        width: 46mm,
        ..c-res,
      ),
      node(
        (2, 12),
        step(
          [점검],
          [연료 감소 상한 $sum n_"new" q_"new" R_"avg" + sum A_"conv,cum" q_"conv" >= -1$],
          [상향식 계산과의 일치],
        ),
        name: <r3>,
        width: 46mm,
        ..c-chk,
      ),
      node(
        (1, 13),
        step([결과 보고], [범위별 직접·간접·신재생, 순 효과], [2025--2050 누적, 기본 대비 변화율]),
        name: <r4>,
        width: 46mm,
        ..c-res,
      ),
      node(
        (2, 13),
        step([민감도 분석], [국가 배출계수 전망, 신축 물량 자료], [$R_"deg"$, $k_"use"$, $r_"conv/yr"$, SCOP]),
        name: <r5>,
        width: 46mm,
        ..c-chk,
      ),
      edge(<n5>, <r1>, "-|>"),
      edge(<x5>, <r1>, "-|>"),
      edge(<r1>, <r2>, "-|>"),
      edge(<r1>, <r3>, "-|>"),
      edge(<r2>, <r4>, "-|>"),
      edge(<r4>, <r5>, "-|>"),

      // 영역 표시
      group((0, 1), (1, 3), luma(40)),
      group((0, 4), (0, 9), rgb("#3f8f55")),
      group((2, 4), (2, 9), rgb("#c07a2c")),
      group((0, 11), (2, 13), rgb("#6a4fa3")),
    )
  },
  kind: image,
  caption: [시나리오별 온실가스 감축량 산정 절차],
) <fig:procedure>

#figure(
  table(
    columns: (auto, 1fr, auto),
    align: (center, left, left),
    table.header([단계], [내용], [자료]),
    [1], [KEEI 에너지원별 수요, 주택 수·평균 주거면적을 연도별로 보간(PCHIP)], [`A1.6`, `A1.7`],
    [2], [에너지원 대응(석유 → LPG·등유 분할), 단위 환산(Mtoe → kWh)], [@table:map, 에너지수급통계],
    [3], [ECO2 배출계수로 기준 배출량 $C_"REF" (t, s, f)$(@eq:cref) 계산], [ECO2],
    [4], [재고 지수 $I_"stock" (t, s)$(@eq:stock-index), 누적 신축 연수 $n_"new" (t, s)$(@eq:n-new) 계산], [KEEI],
    [5],
    [유형별 10개년 평균 $A_"new" (b)$와 기준건물 원단위로 수요 비중 $q_"new" (t, b, f)$(@eq:q-new) 계산],
    [건축 통계, 시뮬레이션],

    [6], [유형·등급·에너지원별 변화율 $R_"grade" (b, g, f)$, $R_"PV" (b, g)$(@eq:rate) 표 작성], [시뮬레이션],
    [7],
    [시나리오별 등급 분포 $r^p_"grade" (t_c, b, g)$ → 평균 변화율 $R^p_"avg" (t, b, f)$(@eq:avg-rate) 계산],
    [시나리오 표],

    [8],
    [전환 대상 재고 $A_"elig" (2024, b)$, 연간 전환율 $r^p_"conv/yr" (t_x, b)$
      \ → 누적 전환 면적 $A^p_"conv,cum" (t, b)$(@eq:conv-cum) 계산],
    [건축물대장, 시나리오 표],

    [9], [전환 수요 변화 비중 $q_"conv" (t, b, f)$(@eq:q-conv) 계산], [시뮬레이션(전환 전후)],
    [10],
    [에너지원별 변화량(@eq:saving-new, @eq:pv, @eq:saving-x) → 범위별 합산(@eq:scope)
      \ → 기본 시나리오와의 차이(@eq:main-ref)로 시나리오 배출량 계산],
    [—],

    [11], [연료 감소량 상한(@eq:conv-cap) 점검], [—],
  ),
  caption: [계산 절차],
) <table:procedure>

연구진은 1--9단계의 결과를 입력 시트로 정리해 제공한다.
계산 시트는 long form으로 구성한다.

- 신축 시트: 행은 "시나리오 $p$ × 유형 $b$ × 에너지원 $f$(+ 태양광) × 연도 $t$"이다.
  열은 $C_"REF"$, $n_"new"$, $q_"new"$, $R^p_"avg"$이고, 마지막 열에서 네 값을 곱한다.
- 전전화 시트: 행은 "시나리오 $p$ × 유형 $b$ × 에너지원 $f$ × 연도 $t$"이다.
  열은 $C_"REF"$, $A^p_"conv,cum"$, $q_"conv"$이고, 마지막 열에서 세 값을 곱한다.
- 결과 시트: 두 시트를 배출 범위·부문·연도별로 합산한다.

= 산정식

== 기호

- *괄호 안 인자*는 어느 연도·부문·유형·에너지원·등급의 값인지를 나타낸다(@table:args).
  연도와 무관한 양은 $t$를 생략한다.
- *아래첨자*는 양의 역할(new, conv, grade 등)을, *위첨자 $p$*는 정책 시나리오를 나타낸다.
- 첫 글자로 양의 종류를 구분한다.
  $r$은 비중·비율(0--1), $R$은 변화율(감소 음수, 증가 양수), $k$는 보정계수, $q$는 KEEI 수요 $Q$ 대비 비율, $n$은 연수, $I$는 기준연도 대비 지수이다.

#figure(
  table(
    columns: (auto, 1fr),
    align: (center, left),
    table.header([인자], [의미]),
    [$t$], [산정(운영)연도],
    [$t_c$], [준공(사용승인)연도],
    [$t_p = t_c - n_"lag"$],
    [허가·정책 적용연도.
      $n_"lag"$는 허가→준공 시차(2년)],

    [$t_x$], [전전화 완료연도],
    [$s$], [부문: 주거 "res"(가정), 비주거 "nres"(서비스)],
    [$b$],
    [
      건물 유형.
      신축 유형 집합 $cal(B)_"new" (s)$는 용도·규모·공공/민간으로 구분한다(e.g.
      공동주택-C3-공공).
      \ 전환 대상 유형 집합 $cal(B)_"conv" (s)$는 용도·규모·현재 난방 연료로 구분한다(e.g.
      업무-A3-LNG).
    ],

    [$f$],
    [
      에너지원.
      직접 배출원 $cal(F)_D = {"LNG", "LPG", "kero"}$(등유),
      간접 배출원 $cal(F)_I = {"el", "ht", "dhc"}$(전력, 열, 지역냉난방)
    ],

    [$g$], [성능 등급: "base"(에너지절약설계기준), ZEB5, ZEB4, ZEB3, ZEB2, ZEB1, ZEB+],
    [$p$ (위첨자)],
    [정책 시나리오.
      신축 의무화 시나리오와 전전화 시나리오의 조합],
  ),
  caption: [인자 정의],
) <table:args>

#figure(
  table(
    columns: (auto, 1fr, auto),
    align: (center, left, left),
    table.header([기호], [의미], [정의]),
    [$C_"REF" (t, s, f)$], [기준 배출량(KEEI REF)], [@eq:cref],
    [$C^p (t, s, f)$], [시나리오 배출량], [@eq:main],
    [$Delta C^p_"new"$, $Delta C^p_"PV"$, $Delta C^p_"conv"$],
    [배출 변화량(신축 ZEB, 태양광, 전전화).
      감소는 음수],
    [@section:basic],

    [$Q(t, s, f)$], [KEEI 에너지 수요(kWh)], [@section:cref],
    [$"EF" (f)$], [ECO2 배출계수(kgCO#sub[2]/kWh)], [@section:ef],
    [$I_"stock" (t, s)$], [재고 지수(2024년 = 1)], [@eq:stock-index],
    [$n_"new" (t, s)$], [재고 지수로 가중한 누적 신축 연수(년)], [@eq:n-new],
    [$A_"new" (b)$], [유형별 10개년 평균 신축 연면적(m#super[2]/년)], [@eq:new-base],
    [$e_"base"$, $e_"grade"$, $e_"pre"$, $e_"post"$],
    [원단위(kWh/m#super[2]): 기준건물, 등급별, 전환 전, 전환 후],
    [@section:rate, @section:elec],

    [$q_"new"$, $q_"conv"$],
    [KEEI 수요 대비 신축 기준건물 1년분 수요(1/년), 전환 1 m#super[2]당 수요 변화(1/m#super[2])],
    [@eq:q-new, @eq:q-conv],

    [$R_"grade"$, $R_"PV"$, $R^p_"avg"$], [등급별 변화율, 태양광 변화율, 평균 변화율], [@section:rate],
    [$r^p_"grade"$], [준공 코호트의 등급 분포], [@section:grade],
    [$R_"deg"$, $R_"deg,PV"$], [연간 성능 저하율, 태양광 열화율], [@eq:avg-rate],
    [$A_"elig"$, $A^p_"conv"$, $A^p_"conv,cum"$],
    [전환 대상 재고, 당해 전환 면적, 생존 누적 전환 면적],
    [@section:elec],

    [$r^p_"conv/yr"$, $r_"demo"$], [연간 전환율, 멸실률], [@section:elec],
  ),
  caption: [주요 기호],
) <table:symbols>

== 기본식 <section:basic>

계산은 에너지원 $f$ 단위로 수행하고, 결과를 배출 범위로 합산한다.
시나리오 $p$, 부문 $s$, 에너지원 $f$의 연도 $t$ 배출량은 다음과 같다.

$ C^p (t, s, f) = C_"REF" (t, s, f) + Delta C^p (t, s, f) $ <eq:main>

배출 변화량은 신축 ZEB 의무화 효과와 기존 건물 전전화 효과의 합이다.
두 효과는 서로 다른 건물군(2025년 이후 준공, 2024년 이전 준공)에 적용되므로 겹치지 않는다.

$ Delta C^p (t, s, f) = Delta C^p_"new" (t, s, f) + Delta C^p_"conv" (t, s, f) $ <eq:saving>

$
  Delta C^p_"new" (t, s, f)
  = C_"REF" (t, s, f) dot sum_(b in cal(B)_"new" (s)) n_"new" (t, s) dot q_"new" (t, b, f) dot R^p_"avg" (t, b, f)
$ <eq:saving-new>

$
  Delta C^p_"PV" (t, s)
  = C_"REF" (t, s, "el") dot sum_(b in cal(B)_"new" (s)) n_"new" (t, s) dot q_"new" (t, b, "el") dot R^p_"PV,avg" (t, b)
$ <eq:pv>

$
  Delta C^p_"conv" (t, s, f)
  = C_"REF" (t, s, f) dot sum_(b in cal(B)_"conv" (s)) A^p_"conv,cum" (t, b) dot q_"conv" (t, b, f)
$ <eq:saving-x>

배출 범위별 변화량은 에너지원별 변화량의 합이다.
태양광 상쇄 $Delta C^p_"PV"$는 간접 배출과 따로 보고한다.

$
  Delta C^p_D (t, s) = sum_(f in cal(F)_D) Delta C^p (t, s, f), quad
  Delta C^p_I (t, s) = sum_(f in cal(F)_I) Delta C^p (t, s, f)
$ <eq:scope>

#info[
  *곱셈식의 의미.*
  $n_"new" dot q_"new"$는 "2025년 이후 준공된 신축 건물(기준건물 성능 가정)의 수요가 KEEI 부문 수요에서 차지하는 비율"이다.
  이는 신축 비중 $r_"new" = A_"new,cum" \/ A_"stock"$과 원단위 보정계수 $k_"new" = e_"base" \/ e_"stock"$의 곱과 같다.
  재고 연면적 $A_"stock"$은 두 항 사이에서 약분되므로 계산에 필요 없다.
  또 $C_"REF" = Q dot "EF"$이므로 $Q$도 약분되어, 결과는 상향식 계산과 같다(부록).
  곱셈식으로 쓰는 이유는 결과를 KEEI 기준 배출량 대비 비율로 해석하기 위함이다.
]

#warning[
  *기준 배출량에 변화율을 바로 곱하면 안 된다.*
  $C_"REF"$는 기존 건물을 포함한 부문 전체 배출량이다.
  변화율은 신축 건물에만 적용되므로 반드시 $n_"new" dot q_"new"$를 함께 곱해야 한다.
]

== 재고 지수와 누적 신축 연수 <section:stock>

재고 지수는 2024년 대비 부문 재고의 크기이다.
신축 물량을 재고의 증가 속도에 맞춰 조정하는 데에만 쓴다(@section:new).

$ I_"stock" (t, s) = (A_"stock" (t, s)) / (A_"stock" (2024, s)) $ <eq:stock-index>

- 주거: KEEI 주택 수 $N(t, h)$와 평균 주거면적 $a_"unit" (t)$로 계산한다.
  $ I_"stock" (t, "res") = (sum_h N(t, h) dot a_"unit" (t)) / (sum_h N(2024, h) dot a_"unit" (2024)) $
  $h$는 KEEI 주택 유형(단독, 아파트, 비아파트 공동주택)이다.
  면적 기준(전용/연면적)은 약분된다.
  2050년 값은 약 1.03이다.
- 비주거: KEEI에 연면적 지표가 없으므로 서비스부문 에너지 수요로 대신한다.
  $ I_"stock" (t, "nres") = (sum_f Q(t, "nres", f)) / (sum_f Q(2024, "nres", f)) $
  합계는 KEEI의 5개 에너지원(석유, 도시가스, 전력, 지역난방, 신재생·기타)에 대한 것이다.
  2050년 값은 약 1.16이다.

#warning[
  KEEI 수요에는 원단위 개선이 반영되어 있어, 비주거 재고 지수는 재고 증가를 과소평가할 수 있다.
  2024년 대비 2050년 서비스 산출액은 약 31%, 에너지 수요는 약 16% 늘어난다.
  신재생·기타를 합계에 포함하는 이유는, 태양광 자가발전과 히트펌프 환경열이 구매 전력·연료를 대체해도 합계가 크게 변하지 않기 때문이다.
]

신축 연면적이 직전 연도 재고에 비례한다고 보면($A_"new" (t_c, b) = A_"new" (b) dot I_"stock" (t_c - 1, s)$), 누적 신축 연면적은 다음과 같다.

$
  A_"new,cum" (t, b) = A_"new" (b) dot n_"new" (t, s), quad n_"new" (t, s) = sum_(t_c = 2025)^(t) I_"stock" (t_c - 1, s)
$ <eq:n-new>

- $n_"new" (t, s)$는 재고 지수로 가중한 누적 신축 연수(년)로, 부문에만 의존한다.
- 직전 연도($t_c - 1$) 재고를 쓰므로 첫 코호트(2025년 준공)의 신축량은 10개년 평균 $A_"new" (b)$와 같다($I_"stock" (2024) = 1$).
- 2025년 이후 준공 건물은 2050년까지 멸실되지 않는다고 본다(경과 연수가 최대 25년).

== 수요 비중 $q_"new"$ <section:q>

신축 기준건물 1년분의 에너지 사용량이 KEEI 부문 수요에서 차지하는 비중이다.

$ q_"new" (t, b, f) = (A_"new" (b) dot e_"base" (b, f)) / (Q(t, s, f)) quad ["1/년"] $ <eq:q-new>

- $e_"base" (b, f)$: 유형 $b$ 기준건물의 에너지원 $f$ 원단위(kWh/m#super[2], @section:rate).
  전력은 소요량과 태양광 발전량(음수)을 더한 순 전력이다.
- $q_"new"$는 에너지원 단위로 정의한다.
  에너지원을 배출 범위로 먼저 합산한 $Q$를 분모로 쓰면, 신축 건물의 에너지원 구성 대신 KEEI 부문의 에너지원 구성으로 배출계수가 가중평균되어 결과가 왜곡된다.
- ECO2는 5대 용도(난방, 냉방, 급탕, 조명, 환기)만 평가하고 KEEI 수요는 모든 용도를 포함한다.
  그러나 $q_"new"$와 $C_"REF"$에 모두 같은 $Q$가 들어가므로 5대 용도 비율은 약분되어, 따로 곱할 필요가 없다.

#warning[
  KEEI 수요가 0인 에너지원(e.g.
  지역냉방)은 $q_"new"$가 정의되지 않는다.
  이런 에너지원은 상향식 식($n_"new" dot A_"new" dot e_"base" dot "EF" dot R^p_"avg"$, 부록)으로 직접 계산한다.
]

= KEEI 전망값의 연도 보간 <section:interp>

KEEI 엑셀 파일은 2000, 2024, 2030, 2040, 2050년 값만 제공한다.
산정은 2025--2050년 매년 수행하므로, 2024년 이후 절점을 *PCHIP*(piecewise cubic Hermite interpolating polynomial)으로 보간한다.
PCHIP은 절점 사이에서 단조성을 유지하고 넘침(overshoot)과 음수가 생기지 않는다.
선형·로그선형·3차 스플라인과의 차이는 약 1% 이내로, 결과에 큰 영향을 주지 않는다.

+ *원자료만 보간한다.*
  에너지원별 수요, 주택 수, 평균 주거면적 등 KEEI가 직접 제시한 값을 보간하고, 재고 지수 등 파생값은 연도마다 다시 계산한다.
+ *2000년 값은 절점으로 쓰지 않는다.*
  2000--2024년은 간격이 너무 길다.
+ *보간 결과를 입력 시트로 제공한다.*
  PCHIP은 엑셀 기본 함수가 아니므로 Python으로 미리 계산한다 \
  (`scipy.interpolate.PchipInterpolator`).

KEEI에 연도별 원자료를 요청하고, 확보하면 보간값을 대체한다.

= 입력 항목의 산정

== 기준 배출량 $C_"REF" (t, s, f)$ <section:cref>

KEEI 에너지원을 ECO2 에너지원에 @table:map 과 같이 대응시킨다.

#figure(
  table(
    columns: (auto, auto, auto, 1fr),
    align: (left, left, center, left),
    table.header([KEEI 항목], [에너지원 $f$], [범위], [비고]),
    [도시가스], [LNG], [직접], [],
    [석유], [LPG, 등유(kero)], [직접], [『에너지수급통계』의 부문별 제품 소비 비중 $r_"oil" (s, f)$로 분할],
    [석탄],
    [—],
    [직접],
    [주거 0.16 Mtoe(2024) → 2030년 소멸.
      제외하거나 국가 계수 적용],

    [전기], [el], [간접], [부문별 전력 수요],
    [지역난방], [ht 또는 dhc], [간접], [ECO2의 '열'·'지역냉난방' 정의에 맞춰 배정],
    [신재생·기타],
    [—],
    [—],
    [배출 0.
      건물 태양광 자가발전, 히트펌프 환경열 등],
  ),
  caption: [KEEI 에너지원과 ECO2 에너지원의 대응],
) <table:map>

$ C_"REF" (t, s, f) = Q(t, s, f) dot "EF" (f) $ <eq:cref>

- $Q(t, s, f)$는 부문 $s$의 에너지원 $f$ 수요(kWh)이다.
  KEEI 수요(Mtoe)에 11.63 TWh/Mtoe를 곱해 환산한다.
- KEEI '전기'는 자가발전을 뺀 구매 전력이다.
  건물 태양광 자가발전은 '신재생·기타'로 집계된다.
- KEEI 통계는 총발열량 기준이다.
  ECO2 배출계수의 발열량 기준이 다르면 환산한다.
- KEEI `A1.14`의 직접 배출량(국가 인벤토리 방식)은 검증용으로만 쓴다.

#figure(
  table(
    columns: (auto, auto, 1fr, 1fr, 1fr, 1fr),
    align: (left, left, right, right, right, right),
    table.header([부문], [항목], [2024], [2030], [2040], [2050]),
    table.cell(rowspan: 5)[주거\ (가정)\ `A1.6`], [도시가스 (Mtoe)], [10.02], [10.34], [9.79], [9.21],
    [석유 (Mtoe)], [1.75], [1.31], [0.87], [0.63],
    [석탄 (Mtoe)], [0.16], [0.02], [0.00], [0.00],
    [전력 (TWh)], [84.4], [90.0], [94.2], [91.9],
    [지역난방 (TWh)], [24.3], [28.1], [29.5], [30.0],
    table.hline(),
    table.cell(rowspan: 4)[비주거\ (서비스)\ `A1.7`], [도시가스 (Mtoe)], [3.55], [4.84], [6.00], [6.07],
    [석유 (Mtoe)], [3.14], [2.10], [0.89], [0.42],
    [전력 (TWh)], [182.4], [203.3], [216.8], [206.2],
    [지역난방 (TWh)], [6.4], [6.3], [6.4], [6.1],
  ),
  caption: [KEEI REF 에너지원별 수요],
) <table:keei>

== 배출계수 <section:ef>

작년 연구와의 연속성과 인증 제도와의 통일성을 위해 ECO2에 내장된 에너지원별 배출계수를 연도와 관계없이 적용한다.

$ "EF" (f) = "EF"_"ECO2" (f) quad (f in {"el", "ht", "dhc", "LNG", "LPG", "kero"}) $ <eq:ef-a>

변화율을 구하는 시뮬레이션도 ECO2 결과이므로, 기준 배출량과 변화율이 같은 계수 체계를 쓴다.
고정 계수는 전력 부문 탈탄소화를 반영하지 않으므로, 2040년 이후 간접 감축량과 태양광 상쇄량은 크게 나온다.
그 차이는 국가 배출계수 전망을 쓴 민감도 분석으로 함께 제시한다(@section:sens).

== 신축 연면적 $A_"new" (b)$ <section:new>

신축 물량은 경기·금리·부동산 시장의 영향으로 연도별 변동이 커서 직접 예측하기 어렵다.
따라서 유형별 최근 10개년 실적의 평균을 기준값으로 쓴다.

$ A_"new" (b) = 1 / 10 sum_(t' = 2015)^(2024) A_"obs" (t', b) $ <eq:new-base>

- $A_"obs" (t', b)$: 연도 $t'$ 유형 $b$(용도·규모·공공/민간)의 신축 연면적.
  건축구분이 신축인 건만 집계한다.
- 허가 기준 자료에는 착공하지 않거나 취소된 물량이 포함된다.
  가능하면 사용승인(준공) 기준으로 집계하고, 허가 기준이면 준공 대비 비율을 확인한다.
- 주거는 세대수 구간(C1--C4) 분류를 위해 연면적과 함께 세대수를 집계한다.
- 유형별 합계를 국토교통부 건축 허가·준공 통계의 용도별 신축 연면적과 대조해 누락·중복을 점검한다.

연도 $t_c$의 신축 연면적은 기준값을 재고 지수로 조정한 값이다.

$ A_"new" (t_c, b) = A_"new" (b) dot I_"stock" (t_c - 1, s) quad (t_c = 2025, ..., 2050) $ <eq:new-index>

이는 유형별 연간 신축률(직전 연도 재고 대비)이 일정하다는 가정이다.
KEEI 재고를 비율로만 쓰므로 과거 재고 통계와 KEEI 재고의 면적 기준 차이는 약분된다.
허가→준공 시차 $n_"lag"$는 각 준공 코호트에 적용되는 정책 연도($t_p = t_c - n_"lag"$)를 정하는 데에만 쓴다.

== 기준건물 원단위와 등급별 변화율 <section:rate>

시뮬레이션(ECO2)으로 유형 $b$의 기준건물($g = "base"$)과 등급별 건물의 에너지원별 원단위를 구한다.
실사용 보정계수 $k_"use"$가 있으면 적용한다.

- 연료와 열($f != "el"$): $e_"base" (b, f) = e (b, "base", f)$
- 전력: 태양광 발전량 $e_"PV"$를 음수로 두고, 소요량 $e_"cons"$와 더한 *순 전력*을 기준값으로 쓴다.
  KEEI '전기'가 구매 전력이므로 같은 기준이 된다.
  $ e_"base" (b, "el") = e_"cons" (b, "base") + e_"PV" (b, "base") $

등급별 변화율은 같은 범위의 기준값과의 차이를 기준건물 원단위로 나눈 값이다.

$
  R_"grade" (b, g, f) = (e (b, g, f) - e (b, "base", f)) / (e_"base" (b, f)), wide
  R_"PV" (b, g) = (e_"PV" (b, g) - e_"PV" (b, "base")) / (e_"base" (b, "el"))
$ <eq:rate>

- 전력의 $R_"grade" (b, g, "el")$은 소요량 $e_"cons"$의 차이로 계산한다.
- 전력과 태양광은 분모가 같으므로 $R_"grade" (b, g, "el") + R_"PV" (b, g)$는 순 전력의 변화율이다.
- 기준건물($g = "base"$)에서는 모든 변화율이 0이다.
  계산 후 이 조건을 점검한다.
- 연료와 열은 $R_"grade" = e (b, g, f) \/ e_"base" (b, f) - 1$과 같다.
- 배출계수가 고정이므로 에너지 기준과 배출 기준의 변화율은 같다.

#info[
  분모를 범위별 기준값으로 두고 $e \/ e_"base" - 1$을 쓰면 안 된다.
  전력처럼 분모가 소요량과 발전량의 합이면, 기준건물에서도 변화율이 0이 되지 않아 순 전력 전체가 감소한 것으로 계산된다.
]

=== 시나리오별 등급 분포 $r^p_"grade" (t_c, b, g)$ <section:grade>

준공연도 $t_c$에 유형 $b$ 신축 면적 중 등급 $g$가 차지하는 비율이다(합계 1).
의무 등급은 허가연도 $t_p = t_c - n_"lag"$의 시나리오 값을 따른다.

- 의무 대상: 의무 등급 $g^*$에 커버리지 $r_"cov" (b)$(유형 안의 의무 대상 면적 비율)를 배정한다.
- 비의무 대상: 자발적 인증 비율 $r_"vol" (b)$를 과거 인증 등급 분포로 배분한다.
- 나머지는 "base"로 둔다.
- 민간 "ZEB 5등급 수준 설계기준"은 성능상 ZEB5로 취급한다.

=== 평균 변화율 $R^p_"avg" (t, b, f)$

연도 $t$까지 준공된 코호트의 변화율을 신축 면적으로 가중평균하고, 성능 저하를 반영한다.

$
  R^p_"avg" (t, b, f) = sum_(t_c = 2025)^(t) r_"cohort" (t_c, t)
  dot (1 + R_"deg")^(t - t_c)
  dot sum_g r^p_"grade" (t_c, b, g)
  dot R_"grade" (b, g, f) \
  r_"cohort" (t_c, t) = (I_"stock" (t_c - 1, s)) / (n_"new" (t, s))
$ <eq:avg-rate>

- $r_"cohort" (t_c, t)$는 연도 $t$까지 누적된 신축 면적 중 $t_c$년 준공분의 비중이다.
- $R_"deg"$는 연간 성능 저하율로, 효과가 줄어드는 것이므로 음수로 넣는다(작년 연구 $-0.05$).
- 태양광의 $R^p_"PV,avg"$는 $R_"grade"$ 대신 $R_"PV"$를, $R_"deg"$ 대신 패널 열화율 $R_"deg,PV"$(연 $-0.005$ 내외)를 쓴다.

== 기존 건물 전전화 전환 <section:elec>

=== 개념과 대상

전전화 전환은 2024년 이전 준공 건물의 난방·급탕·취사 설비를 전기 설비(히트펌프, 전기 급탕기, 인덕션 등)로 바꾸는 것이다.
연료(LNG, LPG, 등유) 사용은 줄고 전력 사용은 늘어나므로, 에너지원마다 부호가 다른 변화를 다룬다.
전환 대상 유형은 현재 난방 연료로 구분하며, 지역난방·전기 난방 건물은 제외한다.

=== 전환 면적

$ A^p_"conv" (t_x, b) = r^p_"conv/yr" (t_x, b) dot A_"elig" (t_x - 1, b) $ <eq:conv-area>

$ A_"elig" (t_x, b) = (1 - r_"demo" (s)) dot A_"elig" (t_x - 1, b) - A^p_"conv" (t_x, b) $ <eq:conv-stock>

$ A^p_"conv,cum" (t, b) = sum_(t_x = 2025)^(t) A^p_"conv" (t_x, b) dot (1 - r_"demo" (s))^(t - t_x) $ <eq:conv-cum>

- $r^p_"conv/yr" (t_x, b)$: 연간 전환율(시나리오 변수).
  REF 추세를 넘어서는 *추가* 전환율로 정의한다.
- $A_"elig" (2024, b)$: 2024년 전환 대상 재고.
  건축물대장과 건물 에너지 사용량 자료로 구한다.
- $r_"demo" (s)$: 연간 멸실률.
  기존 건물은 노후 건물이므로 2050년 이전에 멸실될 수 있다.

=== 수요 변화 비중 $q_"conv"$

전환 1 m#super[2]당 에너지원별 사용량 변화가 KEEI 부문 수요에서 차지하는 비중이다.

$ q_"conv" (t, b, f) = (e_"post" (b, f) - e_"pre" (b, f)) / (Q(t, s, f)) quad ["1/m"^2] $ <eq:q-conv>

$e_"pre"$, $e_"post"$는 유형 $b$ 대표 건물의 전환 전후 원단위(kWh/m#super[2])이다.
연료는 $q_"conv" < 0$, 전력은 $q_"conv" > 0$, 열·지역냉난방은 0이다.
전환 전에 쓰지 않던 에너지원이 생겨도($e_"pre" = 0$) 차이로 정의하므로 계산할 수 있다.

=== 순 감축 조건

LNG 난방 건물을 전환할 때, 전환 1 m#super[2]의 순 배출 변화량이 음수가 될 조건은 다음과 같다.

$
  (e_"post" ("el") - e_"pre" ("el")) / (e_"pre" ("LNG") - e_"post" ("LNG")) < ("EF" ("LNG")) / ("EF" ("el"))
$ <eq:conv-breakeven>

좌변은 줄어든 LNG 1 kWh를 대체하는 데 필요한 전력량으로, 대략 보일러 효율 ÷ 히트펌프 연간 성능계수(SCOP)이다.
전력 배출계수가 LNG보다 커서 우변은 1보다 작으므로, 히트펌프 성능이 충분해야 순 감축이 생긴다.
고정 배출계수에서는 순 효과가 0에 가깝거나 양수로 나올 수 있으므로, 사전에 우변과 시뮬레이션의 좌변을 비교해 둔다.

=== 시나리오와 결과 해석

전전화 시나리오는 유형별 연간 전환율의 경로로 정의한다(구체적인 대상과 전환율은 연구진이 결정).

- X0: 추가 전환 없음(REF와 같음)
- X1: 공공 건축물 우선 전환(그린리모델링 사업 연계)
- X2: X1 + 민간 대규모 건물
- X3: X2 + 주거(개별 난방 공동주택·단독주택)

전전화는 직접 배출을 줄이고 간접 배출을 늘린다.
따라서 결과는 직접 배출 감소(건물부문), 간접 배출 증가(전환부문), 순 효과를 나누어 제시한다.

=== 유의 사항

+ *KEEI REF의 전기화 추세.*
  REF에서도 가정부문 전력 비중은 33.3%(2024)에서 37.4%(2050)로 늘고, 서비스부문에는 히트펌프 보급 정책 기조가 반영되어 있다.
  전환율은 이를 넘어서는 추가 전환으로 정의한다.
+ *연료 감소량의 상한.*
  줄어드는 연료 배출은 REF의 해당 연료 배출을 넘을 수 없다.
  연료 $f in cal(F)_D$에 대해 매년 다음을 확인한다.
  $
    sum_(b in cal(B)_"new" (s)) n_"new" (t, s) q_"new" (t, b, f) R^p_"avg" (t, b, f)
    + sum_(b in cal(B)_"conv" (s)) A^p_"conv,cum" (t, b) q_"conv" (t, b, f) >= -1
  $ <eq:conv-cap>
  넘는 경우에는 전환 대상 재고나 전환율을 다시 검토한다.
+ *히트펌프 성능 저하*는 기본값 0으로 두고 민감도 분석에서 다룬다.

= 계산 예시 (가상 수치)

*신축.*
2040년 비주거, 상업 유형 하나, LNG를 가정한다.

- $Q(2040, "nres", "LNG") = 6.00 "Mtoe" = 69.8 "TWh"$, $"EF" ("LNG") = 0.202$ kgCO#sub[2]/kWh → $C_"REF" = 14.10$ MtCO#sub[2]
- $A_"new" (b) = 9.6 times 10^6$ m#super[2]/년, $e_"base" (b, "LNG") = 11.6$ kWh/m#super[2] → $q_"new" = 1.60 times 10^(-3)$/년
- $n_"new" (2040, "nres") = 17.6$년(2025--2040년, 비주거 재고 지수 가중)
- $R^p_"avg" (2040, b, "LNG") = -0.05$

$ Delta C^p_"new" (2040, "nres", "LNG") = 14.10 times 17.6 times (1.60 times 10^(-3)) times (-0.05) = -0.020 " MtCO"_2 $

상향식으로 계산해도 $9.6 times 10^6 times 17.6 times 11.6 times 0.202 times (-0.05) \/ 10^9 = -0.020$ MtCO#sub[2]로 같다.

*전전화.*
2040년 주거, LNG 개별 난방 공동주택 한 유형을 가정한다.

- $A^p_"conv,cum" (2040, b) = 5.0 times 10^7$ m#super[2]
- $e_"pre" ("LNG") = 90$, $e_"post" ("LNG") = 0$, $e_"pre" ("el") = 30$, $e_"post" ("el") = 55$ kWh/m#super[2]

$
  Delta C^p_"conv" (2040, "res", "LNG") & = 5.0 times 10^7 times (0 - 90) times 0.202 \/ 10^9 = -0.91 " MtCO"_2 \
   Delta C^p_"conv" (2040, "res", "el") & = 5.0 times 10^7 times (55 - 30) times 0.469 \/ 10^9 = +0.59 " MtCO"_2
$

순 변화량은 $-0.32$ MtCO#sub[2]이다(전력 배출계수 0.469 가정).
곱셈식(@eq:saving-x)으로 계산해도 $Q$가 약분되어 같은 값이 나온다.

= 기준 시나리오와의 관계(중복 계상 방지) <section:ref>

KEEI 보고서는 REF의 정책 가정을 다음과 같이 밝히고 있다(PDF 쪽수).

- REF는 현재 시행 중이거나 시행이 확정된 정책 기조가 유지된다는 가정의 기준선이다(p\. ~5, 11).
- 서비스부문 전망에는 *제로에너지건축물 의무화 제도 확대*, 건물 자가발전 확대, *히트펌프 보급 활성화 정책 기조*가 반영되었다(p\. ~68).
- 가정부문은 ZEB 의무화의 반영 여부를 명시하지 않았다.

따라서 REF는 현행 ZEB 로드맵(기본 시나리오)을 포함하는 것으로 본다.
강화 시나리오와 전전화 시나리오는 기본 시나리오와의 *차이*만 변화량으로 계산한다.

$ C^p (t, s, f) = C_"REF" (t, s, f) + [Delta C^p (t, s, f) - Delta C^"기본" (t, s, f)] $ <eq:main-ref>

- $Delta C^"기본"$: 기본 시나리오(신축 의무화 기본, 전전화 X0)의 배출 변화량.
  $Delta C^"X0"_"conv" = 0$이다.
- 기본 시나리오의 효과("ZEB 미적용" 대비)는 REF에 이미 포함되어 있으므로 참고값으로만 보고한다.

#warning[
  KEEI는 REF에 반영한 ZEB 물량·등급 경로나 히트펌프 보급 규모를 공개하지 않았다.
  REF가 기본 시나리오의 일부만 반영했다면 @eq:main-ref 는 강화 시나리오의 효과를 과소평가한다.
  특히 가정부문은 기본 시나리오의 태양광 효과가 KEEI 가정부문 신재생 증가분보다 클 수 있으므로, KEEI에 정책 반영 가정을 확인한다.
]

= 결과 지표

+ 연도별 배출 변화량(MtCO#sub[2], 음수 = 감축): 범위별($D$, $I$, PV)·에너지원별·부문별
+ 2025--2050년 누적 배출 변화량
+ 동일 연도 REF(= 기본 시나리오) 대비 변화율: $sum_f [Delta C^p (t, s, f) - Delta C^"기본" (t, s, f)] \/ sum_f C_"REF" (t, s, f)$
+ 효과 구분: 신축 ZEB 의무화($Delta C^p_"new"$, $Delta C^p_"PV"$)와 기존 건물 전전화($Delta C^p_"conv"$)를 따로 제시한 뒤 합산
+ 정책적 해석: 직접 배출 감축은 건물부문(NDC) 기여분, 간접 배출 감축과 태양광 상쇄는 전환부문 기여분이다.
  고정 배출계수 결과는 인증 제도 기준의 감축 잠재량이다.

= 민감도 분석 <section:sens>

- *전력 배출계수 경로.*
  전력 배출계수를 KEEI REF 발전·열생산 부문 배출원단위의 변화율로 낮춘다.
  $
    "EF" (t, "el") = "EF"_"국가" (t_0, "el") dot (I_"PH" (t)) / (I_"PH" (t_0)), quad I_"PH" (t) = (C_"PH" (t)) / (Q_"el,gen" (t) + 11.63 dot Q_"ht,gen" (t))
  $ <eq:ef-b>
  $C_"PH"$는 발전·열생산 배출량(`A1.14`), $Q_"el,gen"$은 총발전량(TWh, `A1.11`), $Q_"ht,gen"$은 열생산량(Mtoe, `A1.12`)이다.
  지수는 2050년 약 0.37이다.
  연료와 열은 고정 계수를 쓴다.
- *성능 저하율* $R_"deg"$($-0.05$--0).
  2050년 평균 코호트 연령이 약 13년이어서 효과가 절반 가까이 줄어드는, 영향이 큰 가정이다.
- *신축 물량*: 자료 출처(공공·민간 구분 자료), 평균 기간(5·10·15년), 허가 대 준공 기준
- *허가–준공 시차* $n_"lag"$, *실사용 보정계수* $k_"use"$
- *전전화*: 연간 전환율, 히트펌프 성능(SCOP), 전환 대상 재고, 멸실률

#pagebreak(weak: true)

#heading(numbering: none)[
  부록.
  상향식 산정과의 등가성
]

$C_"REF" = Q dot "EF"$와 @eq:q-new 를 @eq:saving-new 에 대입하면 다음과 같다(에너지원 $f$, 유형 $b$).

$
  C_"REF" dot n_"new" dot q_"new" dot R^p_"avg"
  = (Q dot "EF") dot n_"new" dot (A_"new" dot e_"base") / Q dot R^p_"avg"
  = A_"new,cum" dot e_"base" dot "EF" dot R^p_"avg"
$ <eq:equiv>

전전화도 같다.

$
  C_"REF" dot A^p_"conv,cum" dot q_"conv"
  = (Q dot "EF") dot A^p_"conv,cum" dot (e_"post" - e_"pre") / Q
  = A^p_"conv,cum" dot (e_"post" - e_"pre") dot "EF"
$ <eq:equiv-x>

즉 곱셈식은 "면적 × 원단위 × 배출계수 × 변화율"의 상향식 계산과 수학적으로 같고, KEEI 수요 $Q$는 약분된다.
KEEI 전망이 변화량의 크기에 영향을 주는 경로는 $n_"new"$(재고 지수) 하나뿐이다.
계산 시트에는 상향식 검증 열을 두어 두 값이 일치하는지 확인한다.
