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

  set heading(numbering: "1.")
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

#align(right)[2026-11-04]

= 개요

== 목적 및 기본 발상

본 문서는 ZEB 인증 의무화 정책 시나리오별 건물부문 온실가스 감축량을 산정하는 방법을 정리한다.
작년 연구는 신축 허가 물량(10개년 평균)과 2018년 기준 원단위로 배출량을 상향식으로 합산했다.
올해(2026년)는 에너지경제연구원(KEEI) 『2025 장기 에너지전망』 기준 시나리오(REF)의 부문별 에너지 수요를 기준 배출량으로 삼고,
여기에 *신축 건물이 차지하는 비중*과 *ZEB 등급별 변화율*을 곱해 배출 변화량을 산정한다.

$ "배출 변화량" = "기준 배출량" times "신축 비중" times "원단위 보정" times "변화율" $

네 항목은 모두 표 하나로 정리되어, 스프레드시트에서 곱셈만으로 계산할 수 있다.

올해는 신축 건물의 ZEB 의무화에 더해 *기존 건물의 전전화(all-electric) 전환* 효과도 평가한다.
전전화 효과도 같은 구조로 계산한다.
다만 신축 비중 대신 *전환 비중*을, 등급별 변화율 대신 *에너지원별 사용량 변화율*을 쓴다.

$ "전환 효과" = "기준 배출량" times "전환 비중" times "원단위 보정" times "에너지원별 변화율" $

전전화는 화석연료 사용을 줄이는 대신 전력 사용을 늘린다.
따라서 연료의 변화율은 음(감소), 전력의 변화율은 양(증가)이 되고, 순 효과는 에너지원별 결과의 합으로 구한다(@section:elec).

#info[
  *부호 규칙.*
  본 문서의 변화율($R$로 시작하는 기호)과 배출 변화량($Delta C$)은 모두 증감 방향을 부호로 나타낸다.
  감소는 음수, 증가는 양수이다.
  예를 들어 ZEB 등급 건물의 LNG 사용량이 기준건물보다 15% 적으면 변화율은 $-0.15$이고, 전전화로 전력 사용량이 40% 늘면 $+0.40$이다.
  보고서 그림 등에서 감축 효과를 양수로 보이고 싶을 때는 $-Delta C$를 "감축량"으로 표기한다.
]

== 결정 사항과 확인 사항 <section:decisions>

내부 회의 결정과 KEEI 보고서 검토 결과를 @table:decisions 에 정리했다.

#figure(
  table(
    columns: (auto, 1fr, auto, auto),
    align: (left, left, left, left),
    table.header([항목], [내용], [근거], [반영]),
    [연도 보간], [PCHIP 채택], [내부 회의], [@section:interp],
    [배출계수],
    [ECO2 고정 배출계수(A안) 채택.
      \ 국가 배출계수 전망(B안)은 민감도 분석에만 사용],
    [내부 회의],
    [@section:ef],

    [신축 물량],
    [건축HUB 최근 10개년 평균(작년과 동일).
      \ KEEI 재고 연동 방식은 민감도 분석에만 사용],
    [내부 회의],
    [@section:theta],

    [5대 용도 비율],
    [
      감축량 산정에는 불필요(약분됨).
      \ 실사용 보정과 5대 용도 기준 변화율 표기에만 사용
    ],
    [검토],
    [@section:share5],

    [REF의 정책 반영],
    [REF는 시행 중·시행 확정 정책을 포함.
      \ 서비스부문은 ZEB 의무화 확대·히트펌프 보급 기조 반영을 명시\ → 강화 시나리오는 기본 시나리오와의 *차이*로 산정],
    [KEEI 보고서],
    [@section:ref],
  ),
  caption: [결정 사항과 확인 사항],
) <table:decisions>

#info[
  *결정 사항에 따른 KEEI 자료의 역할.*
  배출계수를 고정하고(A안) 신축 물량을 과거 평균으로 두면, 배출 변화량의 크기는 KEEI 전망과 무관해진다.
  곱셈식의 KEEI 수요는 약분되어 "신축 면적 × 기준건물 원단위 × 배출계수 × 변화율"만 남기 때문(부록 참조).
  이 경우 KEEI 전망은 (i) 기준 배출량과 시나리오 배출량의 수준, (ii) REF 대비 변화율의 분모, (iii) 연료 감소량 상한 점검(@eq:conv-cap)에 쓰인다.
  따라서 결과를 설명할 때는 "KEEI 전망으로 감축량을 추정했다"가 아니라 "KEEI 전망 대비 감축 효과를 제시했다"로 표현하는 것이 정확하다.
]

== 2025 방법과의 차이

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, left, left),
    table.header([구분], [2025년 연구], [2026년 연구(본 방법)]),
    [평가 대상], [신축 건물 ZEB 의무화], [신축 건물 ZEB 의무화 + 기존 건물 전전화 전환],
    [기준 배출량], [2018년 용도별 원단위 × 연면적], [KEEI REF 부문별 에너지원별 수요 기반 배출량],
    [신축 물량], [허가 연면적 10개년 평균(고정)], [건축HUB 10개년 평균(작년과 동일, @section:theta)],
    [배출계수],
    [문헌 배출계수(전력 0.4567 kgCO#sub[2]/kWh)],
    [
      ECO2 고정 배출계수(A안).
      국가 배출계수 전망(B안)은 민감도 분석
    ],

    [에너지원], [전력·열·기타 연료], [전력, 열, 지역냉난방, LNG, LPG, 등유],
    [배출 범위], [합산], [직접 / 간접 / 신재생 상쇄 구분],
    [기준연도], [2018], [2024(KEEI 기준연도)],
  ),
  caption: [2025년 방법과의 비교],
)

= 계산 절차(스프레드시트 구성)

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

      // ① 공통: 기준 배출량
      gtitle((0, 1), [① 공통: 기준 배출량], luma(40)),
      node(
        (1, 1),
        step([연도 보간 (PCHIP)], [2024--2050 연도별 $Q(t, s, f)$], src: [KEEI `A1.6`--`A1.14`]),
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
          [기준 배출량 · 재고 평균 원단위],
          [$C_"REF" = Q dot "EF"$, #h(0.3em) $e_"stock" = Q \/ A_"stock"$],
          src: [ECO2 배출계수(A안)],
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
        step([신축 연면적], [$A_"new" (b)$ = 10개년 평균], src: [건축HUB 2015--2024]),
        name: <n1>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 6),
        step([신축 비중], [$r_"new" = sum A_"new" \/ A_"stock"$]),
        name: <n2>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 7),
        step([원단위 보정], [$k_"new" = e_"base" \/ e_"stock"$], src: [ECO2 신축 기준건물]),
        name: <n3>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 8),
        step(
          [평균 변화율 $R^p_"avg" (t, b, f)$],
          [$R_"grade" times r^p_"grade" times (1 + R_"deg")^(t - t_c)$],
          src: [ECO2 등급별 결과, 신축 시나리오],
        ),
        name: <n4>,
        width: 46mm,
        ..c-new,
      ),
      node(
        (0, 9),
        step([$Delta C_"new" = C_"REF" dot r_"new" dot k_"new" dot R_"avg"$], [태양광 상쇄 $Delta C_"PV"$는 별도]),
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
        step([전환 면적], [$A_"conv" = r_"conv/yr" dot A_"elig"$ (멸실 반영 누적)], src: [전전화 시나리오 X0--X3]),
        name: <x2>,
        width: 46mm,
        ..c-conv,
      ),
      node((2, 7), step([전환 비중], [$r_"conv" = sum A_"conv" \/ A_"stock"$]), name: <x3>, width: 46mm, ..c-conv),
      node(
        (2, 8),
        step(
          [보정 전환 변화율],
          [$R^*_"conv" = (e_"post" - e_"pre") \/ e_"stock"$],
          [연료 $< 0$, 전력 $> 0$],
          src: [ECO2 전환 전·후],
        ),
        name: <x4>,
        width: 46mm,
        ..c-conv,
      ),
      node(
        (2, 9),
        step([$Delta C_"conv" = C_"REF" dot r_"conv" dot R^*_"conv"$]),
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
      edge(<c3>, (1, 7), <n3>, "--|>", label: $e_"stock"$, label-side: right),
      edge(<c3>, (1, 8), <x4>, "--|>", label: $e_"stock"$, label-side: left),
      edge(<c3>, (1, 9), <n5>, "-|>", label: $C_"REF"$, label-side: right),
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
          [연료 감소 상한 $sum r_"new" k_"new" R_"avg" + sum r_"conv" R^*_"conv" >= -1$],
          [묵시적 멸실률 $r^"impl"_"demo"$],
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
        step([민감도 분석], [B안 배출계수, KEEI 재고 연동 신축량], [$R_"deg"$, $k_"use"$, $r_"conv/yr"$, 보간 방법]),
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
    [1],
    [KEEI 원자료를 연도별로 보간(PCHIP):\ 에너지원별 수요, 주택 수·면적, 서비스 산출액, 발전·열생산 배출·발전량],
    [`A1.6`, `A1.7`, `A1.11`, `A1.12`, `A1.14`],

    [2], [에너지원 대응(석유 → LPG·등유 분할) 및 단위 환산], [@table:map, 에너지수급통계],
    [3],
    [ECO2 배출계수 적용 → $C_"REF" (t, s, f)$ 계산\ (B안은 민감도 분석에서 @eq:ef-b 적용)],
    [ECO2, 온실가스종합정보센터, @table:ef],

    [4],
    [
      유형별 10개년 평균 신축량 $A_"new" (b)$, 재고 연면적 $A_"stock" (t, s)$,\
      신축 비중 $r_"new" (t, b)$ 계산, 묵시적 멸실률 점검
    ],
    [건축HUB, KEEI, 건축물통계],

    [5], [유형·에너지원별 원단위 보정계수 $k_"new" (t, b, f)$ 계산], [시뮬레이션, KEEI],
    [6], [유형·등급·에너지원별 변화율 $R_"grade" (b, g, f)$ 표 작성], [시뮬레이션],
    [7], [시나리오별 등급 분포 $r^p_"grade" (t_c, b, g)$ 표 작성 → $R^p_"avg" (t, b, f)$ 계산], [시나리오 표],
    [8],
    [전전화 대상 재고 $A_"elig" (2024, b)$ 산정, 전환율 $r^p_"conv/yr" (t_x, b)$ 설정\ → 전환 비중 $r^p_"conv" (t, b)$ 계산],
    [건축물대장, 에너지 사용량 자료, 시나리오 표],

    [9], [유형·에너지원별 보정 전환 변화율 $R^*_"conv" (t, b, f)$ 계산], [시뮬레이션(전환 전후), KEEI],
    [10],
    [@eq:saving-new, @eq:saving-x, @eq:pv 로 에너지원별 변화량, @eq:scope 로 범위별 합산,\ 기본 시나리오와의 차이(@eq:main-ref)로 시나리오 배출량 계산],
    [—],

    [11], [연료 감소량 상한(@eq:conv-cap) 점검], [—],
  ),
  caption: [계산 절차],
) <table:procedure>

연구진은 1--9단계의 결과를 입력 시트로 정리해 제공한다.
계산 시트는 신축 시트와 전전화 시트로 나눈다.

- 신축 시트: 행은 "유형 $b$ × 연도 $t$", 열은 $r_"new"$와 에너지원별 $k_"new"$, $R^p_"avg"$(6개 에너지원 + 태양광).
  마지막 열에서 $C_"REF" times r_"new" times k_"new" times R^p_"avg"$를 곱한다.
- 전전화 시트: 행은 "유형 $b$ × 연도 $t$", 열은 $r_"conv"$와 에너지원별 $R^*_"conv"$.
  마지막 열에서 $C_"REF" times r^p_"conv" times R^*_"conv"$를 곱한다.

두 시트의 결과를 에너지원별로 더한 뒤 범위별로 합산한다.

= 산정식

== 기호

기호는 다음 규칙으로 쓴다.

- *괄호 안 인자*는 어느 연도·부문·유형·에너지원·등급의 값인지를 나타낸다(@table:args).
  연도와 무관한 양은 $t$를 생략한다.
- *아래첨자*는 양의 역할(new, conv, grade 등)을, *위첨자 $p$*는 정책 시나리오를 나타낸다.
- 첫 글자로 양의 종류를 구분한다.
  $r$은 비중·비율(0--1, 부호 없음), $R$은 변화율(감소 음수, 증가 양수), $k$는 보정계수(배수)이다.

#figure(
  table(
    columns: (auto, 1fr),
    align: (center, left),
    table.header([인자], [의미]),
    [$t$], [산정(운영)연도],
    [$t_c$], [준공(사용승인)연도],
    [$t_p = t_c - n_"lag"$],
    [허가·정책 적용연도.
      $n_"lag"$는 허가→준공 시차(기본 2년)],

    [$t_x$], [전전화 완료연도],
    [$s$], [부문: 주거 "res"(가정), 비주거 "nres"(서비스)],
    [$b$],
    [
      건물 유형.
      신축 유형 집합 $cal(B)_"new" (s)$는 용도·규모·공공/민간으로 구분한다(예: 공동주택-C3-공공).
      \ 전환 대상 유형 집합 $cal(B)_"conv" (s)$는 용도·규모에 현재 난방 연료를 더해 구분한다(예: 업무-A3-LNG).
      \ 분류 수준은 연구진이 결정한다.
    ],

    [$f$],
    [
      에너지원.
      직접 배출원 $cal(F)_D = {"LNG", "LPG", "kero"}$(등유),
      간접 배출원 $cal(F)_I = {"el", "ht", "dhc"}$(전력, 열, 지역냉난방)
    ],

    [$g$], [성능 등급: "base"(기준: 에너지절약설계기준), ZEB5, ZEB4, ZEB3, ZEB2, ZEB1, ZEB+],
    [$h$],
    [KEEI 주택 유형(단독, 아파트, 비아파트 공동주택).
      재고 산정에만 사용],

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
    [$C_"REF" (t, s, f)$], [기준 배출량(KEEI REF)], [@section:cref],
    [$C^p (t, s, f)$], [시나리오 배출량], [@eq:main],
    [$Delta C^p_"new"$, $Delta C^p_"conv"$, $Delta C^p_"PV"$],
    [배출 변화량(신축 ZEB, 전전화, 태양광).
      감소는 음수],
    [@eq:saving],

    [$Q(t, s, f)$], [KEEI 에너지 수요], [@section:cref],
    [$"EF" (f)$],
    [배출계수(kgCO#sub[2]/kWh).
      B안에서는 $"EF" (t, f)$],
    [@section:ef],

    [$A_"stock"$, $A_"new"$, $A_"elig"$, $A^p_"conv"$],
    [재고, 신축, 전환 대상, 전환 연면적],
    [@section:theta, @section:elec],

    [$e_"base"$, $e_"grade"$, $e_"pre"$, $e_"post"$, $e_"stock"$, $e_"PV"$],
    [원단위(kWh/m#super[2]): 신축 기준건물, 등급별, 전환 전, 전환 후, 재고 평균, 태양광 발전량],
    [@section:mu, @section:rate,\ @section:elec],

    [$r_"new"$, $r^p_"conv"$], [신축 비중, 전환 비중(재고 대비)], [@eq:theta, @eq:eta],
    [$r^p_"grade"$, $r_"cov"$, $r_"vol"$], [등급 분포, 의무 대상 비율, 자발적 인증 비율], [@section:rate],
    [$r_"cohort"$, $r^p_"conv/yr"$], [준공연도별 가중치, 연간 전환율], [@eq:avg-rate, @eq:conv-area],
    [$r_"oil"$, $r_"5use"$, $r_"demo"$, $r_"build"$],
    [석유 분할 비중, 5대 용도 비율, 멸실률, 총신축률],
    [@section:cref, @section:share5,\ @section:theta],

    [$R_"grade"$, $R_"PV"$, $R^p_"avg"$], [등급별 변화율, 태양광 변화율, 평균 변화율], [@section:rate],
    [$R_"conv"$, $R^*_"conv"$], [전환 변화율, 보정 반영 전환 변화율], [@section:elec],
    [$R_"deg"$, $R_"deg,PV"$, $R_"deg,HP"$], [성능 저하율(감쇠 인자 $(1 + R_"deg")^(t - t_c)$)], [@section:rate],
    [$k_"new"$, $k_"conv"$, $k_"use"$, $k_"area"$],
    [원단위 보정(신축, 전환), 실사용 보정, 유형별 면적 보정],
    [@section:mu, @section:elec],
  ),
  caption: [주요 기호],
) <table:symbols>

== 기본식

계산은 *에너지원 $f$ 단위*로 수행하고, 결과를 배출 범위(직접·간접·신재생 상쇄)로 합산한다.
시나리오 $p$, 부문 $s$, 에너지원 $f$의 연도 $t$ 배출량은 다음과 같다.

$ C^p (t, s, f) = C_"REF" (t, s, f) + Delta C^p (t, s, f) $ <eq:main>

배출 변화량 $Delta C$는 신축 건물의 ZEB 의무화 효과와 기존 건물의 전전화 효과의 합이며, 배출이 줄면 음수이다.
두 효과는 서로 다른 건물군(2025년 이후 준공 건물, 2024년 이전 준공 건물)에 적용되므로 겹치지 않고 더할 수 있다.

$ Delta C^p (t, s, f) = Delta C^p_"new" (t, s, f) + Delta C^p_"conv" (t, s, f) $ <eq:saving>

$
  Delta C^p_"new" (t, s, f) = C_"REF" (t, s, f) dot sum_(b in cal(B)_"new" (s)) r_"new" (t, b) dot k_"new" (t, b, f) dot R^p_"avg" (t, b, f)
$ <eq:saving-new>

$
  Delta C^p_"conv" (t, s, f) = C_"REF" (t, s, f) dot sum_(b in cal(B)_"conv" (s)) r^p_"conv" (t, b) dot R^*_"conv" (t, b, f)
$ <eq:saving-x>

@eq:saving-x 의 $r^p_"conv" (t, b)$는 전환 비중, $R^*_"conv" (t, b, f)$는 전환에 따른 에너지원별 보정 전환 변화율이다(@section:elec).
연료는 사용량이 줄어 $R^*_"conv" (t, b, f) < 0$, 전력은 사용량이 늘어 $R^*_"conv" (t, b, "el") > 0$이다.
따라서 $Delta C^p_"conv" (t, s, "el") > 0$(배출 증가)이 된다.

신재생(태양광) 상쇄는 전력 간접 배출을 기준으로 따로 계산해 보고한다.

$
  Delta C^p_"PV" (t, s) = C_"REF" (t, s, "el") dot sum_(b in cal(B)_"new" (s)) r_"new" (t, b) dot k_"new" (t, b, "el") dot R^p_"PV" (t, b)
$ <eq:pv>

배출 범위별 변화량은 에너지원별 변화량의 합이다.

$
  Delta C^p_D (t, s) = sum_(f in cal(F)_D) Delta C^p (t, s, f), quad
  Delta C^p_I (t, s) = sum_(f in cal(F)_I) Delta C^p (t, s, f)
$ <eq:scope>

#figure(
  table(
    columns: (auto, auto, 1fr, auto),
    align: (center, left, left, left),
    table.header([항목], [이름], [의미(한 줄 요약)], [산정]),
    [$C_"REF" (t, s, f)$],
    [기준 배출량],
    [KEEI 전망 에너지 수요로 계산한 부문 전체의 에너지원별 배출량],
    [@section:cref],

    [$r_"new" (t, b)$], [신축 비중], [재고 연면적 중 2025년 이후 준공된 유형 $b$ 건물의 비중], [@section:theta],
    [$k_"new" (t, b, f)$], [원단위 보정계수], [신축 기준건물 원단위 ÷ 재고 평균 원단위(에너지원별)], [@section:mu],
    [$R^p_"avg" (t, b, f)$],
    [평균 변화율],
    [시나리오 $p$의 등급 분포를 반영한 신축 건물의 평균 변화율(감소는 음수)],
    [@section:rate],

    [$r^p_"conv" (t, b)$],
    [전환 비중],
    [재고 연면적 중 2025년 이후 전전화된 기존 건물(유형 $b$)의 비중],
    [@section:elec],

    [$R^*_"conv" (t, b, f)$],
    [보정 전환 변화율],
    [전환 전후 에너지원별 사용량 차이 ÷ 재고 평균 원단위.
      연료는 음수, 전력은 양수],
    [@section:elec],
  ),
  caption: [기본식의 구성 항목],
) <table:terms>

#warning[
  *기준 배출량에 변화율을 바로 곱하면 안 된다.*
  $C_"REF"$는 기존 건물을 포함한 *부문 전체* 배출량이다.
  ZEB 등급별 변화율은 신축 건물에만 적용되므로 반드시 신축 비중 $r_"new"$를 함께 곱해야 한다.
  예를 들어 2040년 서비스부문 직접 배출(15.23 MtCO#sub[2]eq)에 변화율 $-15%$를 그대로 곱하면 $-2.28$ Mt가 된다.
  반면 신축 비중 20%, 원단위 보정 0.8을 반영하면 $-0.37$ Mt로, 크기가 약 6배 차이 난다(가상 수치).
]

= KEEI 전망값의 연도 보간 <section:interp>

== 자료의 시간 해상도

KEEI 엑셀 파일은 2000, 2024, 2030, 2040, 2050년 값만 제공한다.
이 중 2000년과 2024년은 실적, 2030년 이후는 전망이다.
반면 산정은 2025--2050년 매년 수행해야 하므로, 기준연도 사이의 연도별 값이 필요하다.

KEEI 보고서는 2000--2050년을 연도별 곡선으로 제시한다(예: 그림 2.26 서비스 부문 에너지 상품별 수요).
따라서 전망 모형은 연도별 값을 산출하는 것으로 보이며, 공개 엑셀 파일에만 기준연도 값이 수록된 것이다.
KEEI에 연도별 원자료를 요청하되, 확보 전까지는 아래의 PCHIP 보간값을 쓴다.
원자료를 확보하면 보간값을 원자료로 바꾸고, @table:interp-compare 와 같은 방식으로 차이를 확인한다.

== 보간 방법의 비교

연도별 원자료를 구할 수 없으면 기준연도 값(이하 절점)을 보간한다.
후보 방법은 @table:interp-methods 와 같다.

#figure(
  table(
    columns: (auto, auto, 1fr),
    align: (left, left, left),
    table.header([방법], [성질], [한계]),
    [선형],
    [가장 단순.
      엑셀에서 바로 계산 가능],
    [절점에서 기울기가 꺾임.
      보고서의 곡선과 모양이 다름],

    [로그선형],
    [
      구간마다 연평균 증가율이 일정.
      \ KEEI가 증가율(CAGR)로 요약하는 방식과 일치
    ],
    [절점에서 꺾임.
      값이 0이면 사용할 수 없음(석탄 등)],

    [자연 3차 \ 스플라인],
    [2차 미분까지 연속인 매끄러운 곡선],
    [
      절점 사이에서 값이 넘치는 현상(overshoot)이 생길 수 있음.
      \ 정점 위치가 절점에서 벗어남
    ],

    [*PCHIP*],
    [
      1차 미분까지 연속인 매끄러운 곡선.
      \ 절점 사이에서 단조성이 유지되고 넘침이 없음.
      \ 음수가 생기지 않음
    ],
    [엑셀 기본 함수에는 없음(Python 등으로 미리 계산)],
  ),
  caption: [연도 보간 방법의 비교],
) <table:interp-methods>

보간 방법으로 *PCHIP*(piecewise cubic Hermite interpolating polynomial)*을 채택한다.*
Python에서는 `scipy.interpolate.PchipInterpolator`로 구현할 수 있다.

== 보간 방법에 따른 차이

@table:interp-compare 는 방법별 보간값을 비교한 것이다.
절점 간격이 6--10년으로 짧아 방법 간 차이는 약 1% 이내이다.
따라서 보간 방법의 선택은 결과에 큰 영향을 주지 않으며, 곡선 모양과 재현성을 기준으로 정하면 된다.

#figure(
  table(
    columns: (auto, auto, 1fr, 1fr, 1fr, 1fr),
    align: (left, left, right, right, right, right),
    table.header([항목], [방법], [2027], [2035], [2043], [2047]),
    table.cell(rowspan: 4)[서비스 전력 수요 (TWh)], [선형], [192.8], [210.0], [213.7], [209.4],
    [로그선형], [192.6], [209.9], [213.6], [209.4],
    [자연 3차 스플라인], [193.5], [213.8], [215.4], [210.7],
    [PCHIP], [194.5], [212.6], [216.0], [211.9],
    table.cell(rowspan: 3)[가정 석유 수요 (Mtoe)], [선형], [1.529], [1.088], [0.797], [0.704],
    [로그선형], [1.513], [1.065], [0.789], [0.696],
    [PCHIP], [1.508], [1.055], [0.780], [0.684],
  ),
  caption: [보간 방법별 연도 값 비교 (절점: 2024, 2030, 2040, 2050)],
) <table:interp-compare>

== 보간 적용 원칙

+ *원자료만 보간한다.*
  에너지원별 수요, 주택 수, 평균 주거면적, 서비스 산출액, 발전량·배출량 등 KEEI가 직접 제시한 값을 보간한다.
  재고 연면적($N times a_"unit"$), 원단위, 배출원단위 지수 $I_"PH" (t)$ 같은 파생값은 보간된 원자료로 연도마다 다시 계산한다.
  파생값을 따로 보간하면 원자료와 정합성이 깨진다.
+ *2000년 값은 절점으로 쓰지 않는다.*
  2000--2024년은 간격이 24년으로 너무 길다.
  2024년 이전 기간이 필요하면 『에너지수급통계』 등 연도별 실적을 사용한다.
+ *보간 결과를 입력 시트로 제공한다.*
  PCHIP은 엑셀 기본 함수가 아니다.
  따라서 연구진이 2024--2050년 연도별 보간값을 미리 계산해 입력 시트로 제공한다.
  발주처 담당자는 이 시트를 이용해 곱셈만으로 결과를 재현할 수 있다.

= 입력 항목의 산정

== 기준 배출량 $C_"REF" (t, s, f)$ <section:cref>

=== 에너지원 대응

KEEI의 에너지원 구분과 ECO2의 에너지원 구분은 일치하지 않는다.
따라서 @table:map 과 같이 대응시킨다.

#figure(
  table(
    columns: (auto, auto, auto, 1fr),
    align: (left, left, center, left),
    table.header([KEEI 항목], [에너지원 $f$], [범위], [비고]),
    [도시가스], [LNG], [직접], [],
    [석유],
    [LPG, 등유(kero)],
    [직접],
    [
      KEEI는 석유류를 합산해 제시.
      \ 『에너지수급통계』의 가정·상업 부문 제품별 소비 비중 $r_"oil" (s, f)$로 분할
    ],

    [석탄],
    [—],
    [직접],
    [
      ECO2 계수 없음.
      주거 0.16 Mtoe(2024) → 0.02 Mtoe(2030)로 소멸.
      \ 제외하거나 국가 계수 적용
    ],

    [전기],
    [el],
    [간접],
    [`A1.11`#footnote[KEEI 장기에너지전망 엑셀 파일(`_2025_장기에너지전망_기준시나리오REF.xlsb`)의 시트 이름.] 부문별 전력 수요(TWh)],

    [지역난방],
    [ht 또는 dhc],
    [간접],
    [
      ECO2의 '열'·'지역냉난방' 정의에 맞춰 배정.
      KEEI는 지역냉방을 따로 제시하지 않음
    ],

    [신재생·기타], [—], [—], [배출 0],
  ),
  caption: [KEEI 에너지원과 ECO2 에너지원의 대응],
) <table:map>

KEEI 수요(Mtoe)는 11.63 TWh/Mtoe로 환산한다.
KEEI 에너지 통계는 총발열량 기준이다.
ECO2 배출계수가 어느 발열량 기준으로 정의되었는지 확인하고, 다르면 환산해야 한다.

=== 기준 배출량 계산

에너지원별 기준 배출량은 수요에 배출계수를 곱해 구한다.

$ C_"REF" (t, s, f) = Q(t, s, f) dot "EF" (f) $ <eq:cref>

$Q(t, s, f)$는 부문 $s$의 에너지원 $f$ 수요(kWh)이다.
LPG와 등유는 KEEI 석유 수요를 분할해 구한다: $Q(t, s, "LPG") = r_"oil" (s, "LPG") dot Q(t, s, "석유")$, $Q(t, s, "kero") = r_"oil" (s, "kero") dot Q(t, s, "석유")$.
배출계수는 A안(ECO2 고정 배출계수, @section:ef)을 쓰므로 $"EF" (f)$는 연도와 무관한 상수이다.
B안(민감도 분석)에서만 전력 배출계수가 연도에 따라 변한다.

#figure(
  table(
    columns: (auto, auto, 1fr, 1fr, 1fr, 1fr),
    align: (left, left, right, right, right, right),
    table.header([부문], [항목], [2024], [2030], [2040], [2050]),
    table.cell(rowspan: 6)[주거\ (가정)\ `A1.6`], [도시가스 (Mtoe)], [10.02], [10.34], [9.79], [9.21],
    [석유 (Mtoe)], [1.75], [1.31], [0.87], [0.63],
    [석탄 (Mtoe)], [0.16], [0.02], [0.00], [0.00],
    [전력 (TWh)], [84.4], [90.0], [94.2], [91.9],
    [지역난방 (TWh)], [24.3], [28.1], [29.5], [30.0],
    [직접 배출 (MtCO#sub[2]eq, `A1.14`)], [27.04], [25.87], [23.40], [21.50],
    table.cell(rowspan: 5)[비주거\ (서비스)\ `A1.7`], [도시가스 (Mtoe)], [3.55], [4.84], [6.00], [6.07],
    [석유 (Mtoe)], [3.14], [2.10], [0.89], [0.42],
    [전력 (TWh)], [182.4], [203.3], [216.8], [206.2],
    [지역난방 (TWh)], [6.4], [6.3], [6.4], [6.1],
    [직접 배출 (MtCO#sub[2]eq, `A1.14`)], [16.07], [15.98], [15.23], [14.16],
  ),
  caption: [KEEI REF 기준 배출량 산정용 입력값 (`A1.6`, `A1.7`, `A1.11`, `A1.12`, `A1.14`)],
) <table:keei>

#info[
  `A1.14`의 직접 배출량은 국가 인벤토리 방식의 KEEI 산정값이다.
  A안(ECO2 계수)에서는 @eq:cref 로 직접 배출을 다시 계산하고, `A1.14` 값은 검증용으로만 쓴다.
  국가 고유 연료 배출계수로 대략 계산하면 @eq:cref 의 값은 `A1.14`보다 약 8% 크다.
  발열량 기준과 석유 제품 구성의 차이로 보인다.
]

=== 5대 용도 비율의 처리 <section:share5>

KEEI 수요는 부문 전체의 수요로, 가전·취사·업무기기·데이터센터 등 ECO2가 평가하지 않는 용도를 포함한다.
반면 시뮬레이션(ECO2)은 5대 용도(난방, 냉방, 급탕, 조명, 환기)만 평가한다.
그러나 *배출 변화량을 계산할 때 5대 용도 비율을 따로 곱할 필요는 없다.*

원단위 보정계수 $k_"new"$(@eq:mu)의 분자 $e_"base"$는 5대 용도 원단위이고, 분모 $e_"stock"$는 전체 용도 원단위이다.
따라서 @eq:saving-new 의 곱은 5대 용도에서 생기는 배출 변화량 자체가 된다.
5대 용도 비율 $r_"5use" (s)$를 기준 배출량에 곱하면, 일관성을 위해 $k_"new"$의 분모에도 곱해야 하므로 결국 약분된다.

$
  (r_"5use" (s) dot C_"REF") dot r_"new" dot e_"base" / (r_"5use" (s) dot e_"stock") dot R^p_"avg" = C_"REF" dot r_"new" dot k_"new" dot R^p_"avg"
$ <eq:share5-cancel>

#warning[
  $r_"5use" (s)$를 기준 배출량에만 곱하고 $k_"new"$에는 반영하지 않으면, 배출 변화량의 크기가 $r_"5use" (s)$배(약 0.7배)로 과소평가된다.
  반대로 $k_"new"$ 없이 "기준 배출량 × 5대 용도 비율 × 변화율"로 계산하면, 신축 건물이 재고 평균만큼 에너지를 쓴다고 가정하는 것이 되어 과대평가된다.
]

5대 용도 비율이 실제로 필요한 곳은 다음 두 가지이다.

+ *5대 용도 기준 변화율의 표기.*
  "5대 용도 배출량 대비 변화율"을 제시하려면 $Delta C \/ (r_"5use" (s) dot C_"REF")$로 계산한다.
+ *실사용 보정계수 $k_"use"$.*
  ECO2 평가값과 실제 사용량의 차이를 보정할 때, 실제 사용량 중 5대 용도분을 분리해야 한다.
  $ k_"use" (s) = (r_"5use" (s) dot e_"obs" (s)) / (e_"ECO2" (s)) $ <eq:kappa>
  $e_"obs" (s)$는 최근 준공 건물의 실측 원단위(건물 에너지 사용량 자료), $e_"ECO2" (s)$는 같은 건물군의 ECO2 평가 원단위이다.

*자료원.*

- 주거: KEEI `A1.6`이 용도별 수요(난방/온수, 취사, 냉방, 조명, 기타 가전기기)를 제공한다.
  (난방/온수 + 냉방 + 조명) ÷ 전체로 계산하면 2024년 69.3%, 2050년 66.4%이다.
  환기는 별도 항목이 없어 '기타 가전기기'에 포함된 것으로 보이므로, 이 값은 하한에 가깝다.
  KEEI 전망과 같은 자료이므로 연도별 값을 그대로 쓸 수 있다.
- 비주거: KEEI는 서비스부문 용도별 수요를 공개하지 않는다.
  후보 자료는 『에너지총조사』(국가승인통계, 3년 주기)의 상업·공공 부문 용도별 소비 구성이며, 용도 구분이 5대 용도와 맞는지 확인이 필요하다.
  작년 연구의 70%(선행 연구)는 비교 기준으로 쓴다.
- 서비스부문 KEEI 수요에는 데이터센터 증설에 따른 정보통신업 수요 증가(2040년까지 2024년의 3배 이상)가 반영되어 있다.
  따라서 비주거의 $r_"5use" (s)$는 시간이 지날수록 작아질 가능성이 크고, 고정값을 쓰면 5대 용도 기준 변화율의 크기가 과소평가된다.

== 배출계수 적용 방안 <section:ef>

작년 연구와의 연속성, 인증 제도와의 통일성, 발주처의 의향을 고려해 *A안(ECO2 고정 배출계수)을 채택*했다.
B안은 전력 부문 탈탄소화가 결과에 미치는 영향을 보이기 위한 민감도 분석에만 쓴다(@table:ef-options).

#figure(
  table(
    columns: (auto, auto, auto),
    align: (left, left, left),
    table.header([구분], [A안: ECO2 고정 배출계수], [B안: 국가 배출계수 + 연도별 전망]),
    [배출계수],
    [ECO2에 내장된 에너지원별 고정값 \ (전력, 열, 지역냉난방, LNG, LPG, 등유)],
    [
      연료: 국가 고유 배출계수(고정)\
      전력: 국가 전력 배출계수에 KEEI 기반 변화율 적용
    ],

    [시간 변화], [없음], [전력 배출계수가 연도별로 감소\ (2050년 2024년 대비 약 0.37배)],
    [장점],
    [
      건축물 에너지효율등급·ZEB 인증 평가와 같은 계수.
      \ ECO2 결과와 일관.
      발주처가 이해하고 재현하기 쉬움.
    ],
    [
      전력 부문 탈탄소화를 반영.
      \ 국가 온실가스 인벤토리·NDC와 정합
    ],

    [한계],
    [
      전력 탈탄소화를 반영하지 못함.
      \ 2040년 이후 간접 감축량과 태양광 상쇄량이 과대평가됨
    ],
    [
      미래 전력 배출계수가 전망값이라 불확실.
      \ 산정 단계가 늘어남
    ],

    [적용], [*채택*(주 분석)], [민감도 분석, 감축량 범위 제시],
  ),
  caption: [배출계수 적용 방안 비교],
) <table:ef-options>

=== A안: ECO2 고정 배출계수

$ "EF" (f) = "EF"_"ECO2" (f) quad (f in {"el", "ht", "dhc", "LNG", "LPG", "kero"}) $ <eq:ef-a>

모든 에너지원에 ECO2 배출계수를 연도와 관계없이 적용한다.
변화율 $R_"grade" (b, g, f)$를 계산하는 시뮬레이션도 ECO2 결과이므로, 기준 배출량과 변화율이 같은 계수 체계를 쓰게 된다.

ECO2 전력 배출계수가 2024년 국가 전력 배출계수와 비슷하다고 가정 시,
A안의 2050년 전력 간접 감축량은 B안보다 약 2.7배($approx 1 \/ 0.37$) 크다.
따라서 A안로 주 결과를 제시하더라도, B안 결과를 함께 제시해 그 차이를 밝힐 것을 권장한다.

=== B안: 국가 배출계수와 연도별 전망 <section:ef-b>

연료(LNG, LPG, 등유)는 국가 고유 배출계수를 고정값으로 쓴다.
전력은 공표된 국가 전력 배출계수(온실가스종합정보센터)를 기준값으로 쓴다.
국가 전력 배출계수는 과거 실적에 대해서만 공표되므로 2025년 이후 값은 전망이 필요하다.
본 방법은 KEEI REF의 발전·열생산 부문 배출원단위 변화율을 이용해 전망한다.

$ "EF" (t, "el") = "EF"_"국가" (t_0, "el") dot (I_"PH" (t)) / (I_"PH" (t_0)) $ <eq:ef-b>

$t_0$는 국가 계수가 공표된 최근 연도(또는 2024년)이다.
$I_"PH" (t)$는 KEEI REF 전망에서 *발전·열생산 부문이 공급한 에너지 1 MWh당 배출량*이다.

$ I_"PH" (t) = (C_"PH" (t)) / (Q_"el,gen" (t) + 11.63 dot Q_"ht,gen" (t)) quad ["tCO"_2"/MWh"] $ <eq:intensity>

- $C_"PH" (t)$: 발전·열생산 부문 배출량(MtCO#sub[2]eq, `A1.14` '발전/열생산')
- $Q_"el,gen" (t)$: 총발전량(TWh, `A1.11`)
- $Q_"ht,gen" (t)$: 열생산량(Mtoe, `A1.12`).
  11.63을 곱해 TWh로 환산한다.

$C_"PH"$, $Q_"el,gen"$, $Q_"ht,gen"$는 @section:interp 의 방법으로 각각 보간한 뒤 연도마다 $I_"PH" (t)$를 계산한다.
$I_"PH" (t)$ 자체를 보간하지는 않는다.

#figure(
  table(
    columns: (auto, 1fr, 1fr, 1fr, 1fr),
    align: (left, right, right, right, right),
    table.header([항목], [2024], [2030], [2040], [2050]),
    [발전·열생산 배출 $C_"PH"$ (MtCO#sub[2]eq)], [208.1], [166.1], [127.2], [95.4],
    [총발전량 $Q_"el,gen"$ (TWh)], [645.8], [706.5], [805.0], [804.3],
    [열생산량 $Q_"ht,gen"$ (Mtoe)], [3.30], [3.59], [3.74], [3.76],
    [$I_"PH" (t)$ (tCO#sub[2]/MWh, 참고값)], [0.304], [0.222], [0.150], [0.112],
    [*지수* $I_"PH" (t) \/ I_"PH" (2024)$], [*1.00*], [*0.73*], [*0.49*], [*0.37*],
  ),
  caption: [KEEI REF 발전·열생산 부문 배출원단위 추이],
) <table:ef>

$I_"PH" (t)$의 *수준*은 공식 전력 배출계수와 다르므로, *변화율(지수)만* 사용한다.
수준이 다른 이유는 다음과 같다.

+ *발전단 기준이다.*
  공식 계수는 송배전 손실과 발전소 자체 소비를 반영한 소비단 기준이다.
+ *전력과 열이 합쳐져 있다.*
  `A1.14`는 발전과 열생산 배출을 합산해 제시하고, 열병합발전의 연료를 전력과 열로 나눌 수 없다.
  다만 열생산은 전체 공급의 약 6%이므로 $I_"PH" (t)$는 사실상 전력의 배출원단위 추이를 나타낸다.
+ *배출량 정의가 다를 수 있다*(tCO#sub[2]eq, 자가 소비 처리 등).

열과 지역냉난방은 B안에서도 고정 계수를 권장한다.
KEEI 지역난방 투입 연료는 2050년에도 대부분 가스(`A1.12`)라, 배출원단위의 변화가 작기 때문이다.

#info[
  @eq:ef-b 는 "공식 전력 배출계수가 KEEI 발전부문 배출원단위와 같은 비율로 변한다"는 가정이다.
  KEEI REF 대신 전력수급기본계획이나 NDC의 전원 구성을 쓰면 감소 속도가 달라진다.
  따라서 전력 배출계수 경로는 민감도 분석 대상에 포함한다.
]

== 신축 비중 $r_"new" (t, b)$ <section:theta>

=== 정의

신축 비중은 연도 $t$의 부문 재고 연면적 중 *2025년 이후 준공된* 유형 $b$ 건물이 차지하는 비중이다.

$ r_"new" (t, b) = (sum_(t_c = 2025)^(t) A_"new" (t_c, b)) / (A_"stock" (t, s)) $ <eq:theta>

- $t_c$: 준공(사용승인)연도.
  분자는 2025년부터 $t$년까지 매년 준공된 유형 $b$ 신축 연면적의 누적합이다.
- 합산을 2025년부터 하는 이유: 2024년 이전 준공 건물은 KEEI 기준연도(2024) 재고에 이미 포함되어 있어, 그 성능이 REF 전망에 반영되어 있다.
- 2025년 이후 준공 건물은 2050년까지 멸실되지 않는다고 본다.
  경과 연수가 최대 25년으로 건물 수명보다 짧기 때문.
- $A_"new" (t_c, b)$: 연도 $t_c$의 유형 $b$ 신축(준공) 연면적(@eq:new-avg)
- $A_"stock" (t, s)$: 연도 $t$의 부문 재고 연면적

허가→준공 시차 $n_"lag"$에 따라 2025--2026년 준공분은 2023--2024년 허가분이다.
따라서 이미 시행 중인 의무화의 효과도 일부 포함된다(@section:ref 참조).

=== 신축 연면적 $A_"new" (b)$: 과거 10개년 평균(채택)

신축 물량은 경기·금리·부동산 시장의 영향으로 연도별 변동이 매우 커 예측이 어렵다.
따라서 작년 연구와 같이 건축HUB 최근 10개년 실적의 평균을 매년 같은 값으로 적용한다.

$
  A_"new" (t_c, b) = A_"new" (b) = 1 / 10 sum_(t' = 2015)^(2024) A_"obs" (t', b) quad (t_c = 2025, ..., 2050)
$ <eq:new-avg>

- $A_"obs" (t', b)$: 건축HUB의 연도 $t'$ 유형 $b$ 신축 연면적.
  유형(용도·규모·공공/민간)별로 직접 평균하므로, 부문 총량을 비중으로 나누는 단계가 필요 없다.
- 허가 자료를 쓰면 허가 후 착공하지 않거나 취소되는 물량이 포함되어 준공 물량보다 크다.
  건축HUB에서 사용승인(또는 착공) 연면적을 함께 취득해 허가 대비 비율을 확인하고, 가능하면 사용승인 기준 평균을 쓴다.
- 평균이 상수이므로 허가→준공 시차 $n_"lag"$은 물량에는 영향을 주지 않고, 각 준공 코호트에 적용되는 정책 연도($t_c - n_"lag"$)에만 영향을 준다.
- 주거는 세대수 구간(C1--C4) 분류를 위해 연면적과 함께 건축물 수·세대수를 집계한다.

#info[
  *정합성 점검.*
  신축 물량을 KEEI와 무관하게 고정하면, KEEI 재고 전망과 함께 쓸 때 묵시적 멸실률이 정해진다.
  $
    r^"impl"_"demo" (t, s) = (sum_(b in cal(B)_"new" (s)) A_"new" (b) - [A_"stock" (t, s) - A_"stock" (t - 1, s)]) / (A_"stock" (t - 1, s))
  $ <eq:implied-delta>
  KEEI 주거 재고는 2040년 이후 감소하므로, 그 기간에는 묵시적 멸실률이 커진다.
  $r^"impl"_"demo"$이 과거 멸실 실적에 비해 지나치게 크거나 음수이면, 평균 물량과 KEEI 전망이 서로 맞지 않는다는 뜻이다.
  이 경우 결과 해석에 그 한계를 밝히고, KEEI 재고 연동 방식(아래)으로 민감도를 확인한다.
]

=== 재고 연면적 $A_"stock" (t, s)$

$A_"stock" (t, s)$는 $r_"new"$와 $k_"new"$(@eq:mu)의 분모에 모두 들어가므로 배출 변화량 계산에서 약분된다.
따라서 결과는 $A_"stock"$의 값에 영향을 받지 않는다.
$A_"stock"$는 $r_"new"$와 $k_"new"$를 따로 제시하거나, @eq:implied-delta 의 정합성을 점검할 때만 필요하다.
단, $r_"new"$와 $k_"new"$에는 반드시 *같은* $A_"stock"$를 써야 한다.

*주거.*
`A1.6`의 유형별 주택 수 $N(t, h)$(단독, 아파트, 비아파트 공동주택)와 평균 주거면적 $a_"unit" (t)$로 재고를 구한다.
유형별 평균면적 차이는 2024년 주택총조사의 유형별 평균면적비 $k_"area" (h)$로 보정한다.

$ A_"stock" (t, "res") = sum_h N(t, h) dot a_"unit" (t) dot k_"area" (h) $ <eq:stock-res>

KEEI의 평균 주거면적(75.5 m#super[2], 2024)은 전용면적 기준으로 보인다.
이 값으로 구한 재고는 약 13.8억 m#super[2]로, 건축물통계의 주거 연면적보다 작다.
신축 연면적(건축HUB, 연면적 기준)과 면적 기준이 다르므로, @eq:implied-delta 점검과 $r_"new"$ 표기에는 전용률로 환산해 맞춘다.

*비주거.*
KEEI 서비스부문에는 연면적 지표가 없다.
과거 비주거 연면적(건축물통계)과 KEEI 서비스 산출액 $V_"svc" (t)$(`A1.7`, 업종별 산출액 합계)의 관계를 추정해 재고를 투영한다.

$ ln A_"stock" (t, "nres") = c_0 + c_1 ln V_"svc" (t) $ <eq:stock-nr>

$c_0$, $c_1$는 2005--2024년 자료로 추정한다.
$c_1$는 산출액이 1% 늘 때 비주거 연면적이 늘어나는 비율(탄력성)이다.

=== KEEI 재고 연동 방식(민감도 분석)

민감도 분석에서는 신축 물량을 KEEI 재고 전망과 연동해 추정한다.
재고는 전년 재고에서 멸실분을 빼고 신축분을 더한 것이므로 다음과 같다.

$ A_"stock" (t_c, s) = (1 - r_"demo" (s)) dot A_"stock" (t_c - 1, s) + A_"new" (t_c, s) $ <eq:stock-flow>

$
  A_"new" (t_c, s) = max{0, A_"stock" (t_c, s) - A_"stock" (t_c - 1, s) + r_"demo" (s) dot A_"stock" (t_c - 1, s)}
$ <eq:new>

$r_"demo" (s)$는 연간 멸실률(전년 재고 중 당해 멸실 면적의 비율)이다.
부문 신축량은 유형별 10개년 평균 비중 $r_"type" (b) = A_"new" (b) \/ sum_(b' in cal(B)_"new" (s)) A_"new" (b')$로 배분한다.
멸실 통계가 부족하면 @table:alt 의 ②를 쓴다.

#figure(
  table(
    columns: (auto, auto, auto, 1fr),
    align: (left, left, left, left),
    table.header([방법], [산정식], [필요 자료], [특징]),
    [④ 과거 실적 평균\ (*채택*)],
    [@eq:new-avg],
    [건축HUB 실적],
    [
      작년과 동일.
      경기 변동의 영향을 평균으로 제거.
      KEEI 재고 전망과 연동되지 않음
    ],

    [① 재고–멸실],
    [@eq:new],
    [KEEI 재고, 멸실률],
    [
      KEEI 재고 전망을 직접 반영.
      멸실 통계의 품질에 좌우됨
    ],

    [② 총신축률],
    [$A_"new" (t_c, s) = r_"build" (s) dot A_"stock" (t_c - 1, s)$],
    [재고, 사용승인 실적],
    [
      멸실 통계 불필요.
      $r_"build" (s)$는 2015--2024년 재고 대비 신축 비율의 평균
    ],

    [③ 수명 분포],
    [$r_"demo" (t, s) = sum_(n_"age") r_"age" (t, s, n_"age") dot r_"demo" (n_"age")$],
    [건축물대장,\ 수명 함수],
    [
      노후 재고의 재건축 시기를 반영.
      자료·계산 부담이 큼
    ],
  ),
  caption: [신축 연면적 추정 방법(④ 채택, ①·② 민감도 분석)],
) <table:alt>

== 원단위 보정계수 $k_"new" (t, b, f)$ <section:mu>

신축 기준건물($"base"$)과 재고 평균 건물의 단위면적당 사용량이 다르므로 이를 보정한다.

$
  k_"new" (t, b, f) = (e_"base" (b, f)) / (e_"stock" (t, s, f)), quad e_"stock" (t, s, f) = (Q(t, s, f)) / (A_"stock" (t, s))
$ <eq:mu>

- $e_"base" (b, f)$: 시뮬레이션에서 구한 유형 $b$ 기준건물의 에너지원 $f$ 사용량(kWh/m#super[2]).
  실사용 보정계수 $k_"use"$를 적용한 값이다.
- $e_"stock" (t, s, f)$: KEEI 에너지원별 수요를 재고 연면적으로 나눈 평균 원단위(kWh/m#super[2])
- 에너지원별로 정의하므로 분자와 분모의 배출계수가 같아 약분된다.
  따라서 $k_"new"$는 에너지 원단위의 비로 계산하면 되고, 배출계수 방안(A/B)과 무관하다.
- 면적 기준(전용/연면적)은 분자와 분모가 같아야 한다.
  KEEI 주거 재고가 전용면적 기준이면 시뮬레이션 원단위도 전용면적 기준으로 환산한다.

#info[
  $k_"new" = 1$로 두면 계산은 단순해진다.
  그러나 이는 신축 건물이 재고 평균만큼 에너지를 쓴다고 가정하기 때문에 배출 변화량의 크기가 과대평가된다.
  또한 서비스부문 수요에는 가로등·상하수도 등 건물 외 수요가 섞여 있을 수 있다.
  이 경우에도 $e_"stock"$를 *같은* KEEI 수요로 계산하면 영향이 상쇄된다(부록 참조).
]

#warning[
  KEEI 수요가 0이거나 매우 작은 에너지원(예: 지역냉방)을 신축 건물이 사용하면 $e_"stock" (t, s, f) approx 0$이 되어 $k_"new"$가 발산한다.
  이런 에너지원은 부록의 상향식 식($A_"new" dot e_"base" dot "EF" dot R^p_"avg"$)으로 직접 계산한다.
]

== 평균 변화율 $R^p_"avg" (t, b, f)$ <section:rate>

=== 등급별 변화율 $R_"grade" (b, g, f)$

시뮬레이션 결과로부터 에너지원별로 기준건물 대비 변화율을 구한다.
사용량이 줄면 음수이다.

$
  R_"grade" (b, g, f) = (e_"grade" (b, g, f)) / (e_"base" (b, f)) - 1, wide R_"PV" (b, g) = - (e_"PV" (b, g) - e_"PV" (b, "base")) / (e_"base" (b, "el"))
$ <eq:rate>

여기서 $e_"PV"$는 단위면적당 태양광 발전량(kWh/m#super[2])이다.
태양광은 추가 발전량만큼 전력 간접 배출이 줄어드는 것으로 보고, 기준건물 전력 사용량 대비 비율에 음의 부호를 붙여 변화율로 정의한다.
배출계수가 고정이면 에너지원별 변화율은 에너지 기준과 배출 기준이 같다.

#figure(
  table(
    columns: 4,
    align: (left, right, right, right),
    inset: (x: 4em),
    table.header([등급], [직접 $R_"grade"$], [간접 $R_"grade"$], [신재생 $R_"PV"$]),
    [ZEB5], [−5.0%], [−5.0%], [−12.4%],
    [ZEB4], [−6.4%], [−10.1%], [−31.6%],
    [ZEB3], [−14.3%], [−25.2%], [−42.2%],
    [ZEB2], [−16.6%], [−29.4%], [−55.7%],
    [ZEB1], [−20.0%], [−32.8%], [−67.8%],
    [ZEB+], [−20.5%], [−34.5%], [−81.3%],
  ),
  caption: [등급별 변화율 예시 — 2025년 보고서 표 2.17(교육사회)로 역산(배출 범위 단위)],
) <table:rate>

@table:rate 는 작년 결과로 역산한 배출 범위 단위의 값이다.
올해는 @eq:rate 에 따라 에너지원별로 구한다.

=== 시나리오별 등급 분포 $r^p_"grade" (t_c, b, g)$

준공연도 $t_c$에 유형 $b$ 신축 면적 중 등급 $g$가 차지하는 비율이다(합계 1).
정책 적용연도는 $t_p = t_c - n_"lag"$이다.

- 의무 대상: 의무 등급 $g^*$에 커버리지 $r_"cov" (b)$(셀 안의 의무 대상 면적 비율)를 배정한다.
- 비의무 대상: 자발적 인증 비율 $r_"vol" (b)$를 과거 인증 등급 분포로 배분한다.
- 나머지는 $"base"$로 둔다.
- 민간 "ZEB 5등급 수준 설계기준"은 성능상 $g = "ZEB5"$로 취급한다.

=== 성능 저하와 준공연도별 가중

$
  R^p_"avg" (t, b, f) = sum_(t_c = 2025)^(t) r_"cohort" (t_c, t)
  dot (1 + R_"deg")^(t - t_c)
  dot sum_g r^p_"grade" (t_c, b, g)
  dot R_"grade" (b, g, f) \
  r_"cohort" (t_c, t) = (A_"new" (t_c, s)) / (sum_(t'_c = 2025)^(t) A_"new" (t'_c, s))
$ <eq:avg-rate>

$r_"cohort" (t_c, t)$는 연도 $t$까지 누적된 신축 면적 중 $t_c$년 준공분의 비중이다.
$R_"deg"$는 연간 성능 저하율로, 효과가 줄어드는 것이므로 음수로 넣는다(작년 5% → $R_"deg" = -0.05$).
태양광은 패널 열화율 $R_"deg,PV"$(연 $-0.005$ 내외)를 별도로 적용한다.

== 기존 건물 전전화 전환 <section:elec>

=== 개념과 대상

전전화 전환은 2024년 이전에 준공된 기존 건물의 난방·급탕·취사 설비를 전기 설비(히트펌프, 전기 급탕기, 인덕션 등)로 바꾸는 것이다.
신축 ZEB와 달리 전전화는 에너지원 자체를 바꾼다.
즉, 연료(LNG, LPG, 등유) 사용은 줄고 전력 사용은 늘어난다.
따라서 변화율이 모든 에너지원에서 0 이하인 신축 ZEB와 달리, 전전화는 에너지원마다 부호가 다른 변화율을 다뤄야 한다.

전환 대상 유형 $b$는 부문·용도·규모에 *현재 난방 연료*를 더해 구분한다.
같은 공동주택이라도 개별 LNG 난방 건물과 지역난방 건물은 전환 효과가 전혀 다르기 때문이다.
지역난방·전기 난방 건물은 기본적으로 전환 대상에서 제외한다.

=== 전환 비중 $r^p_"conv" (t, b)$

전환 비중은 연도 $t$의 부문 재고 연면적 중 2025년 이후 전전화된 유형 $b$ 기존 건물의 비중이다.

$
  r^p_"conv" (t, b) = (sum_(t_x = 2025)^(t) A^p_"conv" (t_x, b) dot (1 - r_"demo" (s))^(t - t_x)) / (A_"stock" (t, s))
$ <eq:eta>

$ A^p_"conv" (t_x, b) = r^p_"conv/yr" (t_x, b) dot A_"elig" (t_x - 1, b) $ <eq:conv-area>

- $t_x$: 전환(공사 완료)연도
- $A^p_"conv" (t_x, b)$: 시나리오 $p$에서 연도 $t_x$에 전환된 유형 $b$의 연면적
- $r^p_"conv/yr" (t_x, b)$: 연간 전환율.
  시나리오 변수로, 전환 대상 중 매년 전환되는 면적의 비율이다.
- $A_"elig" (t_x - 1, b)$: 전년 말 기준 아직 전환되지 않은 유형 $b$의 전환 대상 연면적.
  2024년 대상 재고에서 시작해, 매년 멸실분과 전환분을 뺀다.
  $ A_"elig" (t_x, b) = (1 - r_"demo" (s)) dot A_"elig" (t_x - 1, b) - A^p_"conv" (t_x, b) $ <eq:conv-stock>
- $(1 - r_"demo" (s))^(t - t_x)$: 전환된 건물이 이후 멸실되는 효과.
  신축 건물과 달리 기존 건물은 노후 건물이므로 2050년 이전에 멸실될 수 있다.
- 분모 $A_"stock" (t, s)$는 신축 비중 $r_"new"$와 같은 부문 재고 연면적이다.

2024년 전환 대상 재고 $A_"elig" (2024, b)$는 건축물대장(용도·규모·준공연도)과 건물 에너지 사용량 자료(에너지원별 사용 여부)를 결합해 구한다.
에너지원별 사용량 자료가 없는 유형은 『에너지총조사』 등의 난방 연료별 비중으로 배분한다.

=== 보정 전환 변화율 $R^*_"conv" (t, b, f)$

시뮬레이션으로 구한 전환 전후 사용량을 이용해, 에너지원별 사용량 변화를 KEEI 재고 평균 원단위로 나눈다.

$
  R^*_"conv" (t, b, f) = (e_"post" (b, f) - e_"pre" (b, f)) / (e_"stock" (t, s, f)) = k_"conv" (t, b, f) dot R_"conv" (b, f)
$ <eq:lambda>

$
  k_"conv" (t, b, f) = (e_"pre" (b, f)) / (e_"stock" (t, s, f)), quad R_"conv" (b, f) = (e_"post" (b, f)) / (e_"pre" (b, f)) - 1
$ <eq:lambda-decomp>

- $e_"pre" (b, f)$: 유형 $b$ 대표 건물의 전환 전 에너지원 $f$ 사용량(kWh/m#super[2]).
  시뮬레이션 값에 실사용 보정계수 $k_"use,conv"$를 적용한다.
- $e_"post" (b, f)$: 같은 건물의 전환 후 에너지원 $f$ 사용량(kWh/m#super[2])
- $e_"stock" (t, s, f)$: @eq:mu 와 같은 KEEI 재고 평균 원단위
- $k_"conv" (t, b, f)$: 원단위 보정계수.
  신축의 $k_"new" (t, b, f)$와 같은 의미이다.
- $R_"conv" (b, f)$: 에너지원별 사용량 변화율.
  신축의 등급별 변화율 $R_"grade" (b, g, f)$에 대응한다.

#figure(
  table(
    columns: (auto, auto, auto, 1fr),
    align: (left, center, center, left),
    table.header([에너지원], [$R_"conv" (b, f)$], [$R^*_"conv" (t, b, f)$], [해석]),
    [LNG, LPG, 등유],
    [$-1 <= R_"conv" < 0$],
    [음수],
    [난방·급탕·취사를 모두 전환하면 $-1$.
      일부만 전환하면 $-1$과 0 사이],

    [전력],
    [$R_"conv" > 0$],
    [양수],
    [히트펌프 등의 전력 사용 증가.
      예: 사용량이 40% 늘면 $R_"conv" = +0.4$],

    [열, 지역냉난방], [0], [0], [전환 대상에서 제외하므로 변화 없음],
  ),
  caption: [전전화 시 에너지원별 변화율의 부호],
) <table:conv-sign>

#info[
  *$R_"conv"$ 대신 $R^*_"conv"$를 직접 계산하는 이유.*
  전환 전에 쓰지 않던 에너지원($e_"pre" (b, f) = 0$)이 전환 후에 생기면 $R_"conv"$는 정의되지 않는다.
  @eq:lambda 의 $R^*_"conv"$는 사용량 *차이*를 재고 평균으로 나누므로 이런 경우에도 계산할 수 있다.
  따라서 계산 시트에는 $R^*_"conv"$를 입력하고, $k_"conv"$와 $R_"conv"$는 결과 해석과 검증에만 쓴다.
]

=== 순 감축 조건과 배출계수의 영향

전환 건물 1 m#super[2]의 순 배출 변화량은 에너지원별 변화의 합이다.
LNG 난방 건물을 전환하는 경우로 단순화하면 다음과 같다.

$
  Delta c_"conv" = (e_"post" ("LNG") - e_"pre" ("LNG")) dot "EF" ("LNG") + (e_"post" ("el") - e_"pre" ("el")) dot "EF" (t, "el")
$ <eq:conv-net>

첫째 항은 음수(LNG 감소), 둘째 항은 양수(전력 증가)이다.
순 변화량이 음수(순 감축)가 될 조건은 다음과 같다.

$
  (e_"post" ("el") - e_"pre" ("el")) / (e_"pre" ("LNG") - e_"post" ("LNG")) < ("EF" ("LNG")) / ("EF" (t, "el"))
$ <eq:conv-breakeven>

좌변은 "줄어든 LNG 1 kWh를 대체하는 데 필요한 전력량"이다.
대략 보일러 효율 ÷ 히트펌프 연간 성능계수(SCOP)에 해당한다.
우변은 두 에너지원의 배출계수 비이다.
전력 배출계수가 LNG보다 크기 때문에 우변은 1보다 작고, 히트펌프 성능이 충분히 높아야 순 감축이 생긴다.

#warning[
  *전전화 효과는 배출계수 방안(@section:ef)에 크게 좌우된다.*
  A안(ECO2 고정)에서는 우변이 연도와 무관하게 고정된다.
  전력 배출계수가 높게 유지되므로 순 변화량이 0에 가깝거나 양수(배출 증가)로 나올 수 있다.
  B안에서는 전력 배출계수가 낮아지면서 우변이 커지고, 같은 전환이라도 해가 갈수록 순 감축이 커진다.
  A안의 결과가 작게 나온다면 그것은 전전화의 효과가 작다는 뜻이 아니라 고정 계수의 가정 때문일 수 있다.
  따라서 전전화의 주 결과는 A안으로 제시하되, *B안 민감도 결과를 반드시 함께* 제시한다.
  사전 점검으로, ECO2 배출계수로 @eq:conv-breakeven 의 우변을 먼저 계산하고 시뮬레이션의 좌변과 비교해 둔다.
]

=== 배출 범위별 해석

전전화는 배출 범위 사이에서 배출을 옮긴다.

- 직접 배출($cal(F)_D$): 연료 사용이 줄어 *감소*한다.
  NDC 건물부문 감축에 기여한다.
- 간접 배출($cal(F)_I$): 전력 사용이 늘어 *증가*한다.
  전환부문의 부담이 된다.
- 순 효과: 두 범위의 합이며, @eq:conv-breakeven 이 성립할 때만 음수(순 감축)이다.

따라서 결과는 범위별 값과 순 효과를 모두 보고한다.
범위를 합산한 값만 제시하면 "건물부문은 크게 감축되지만 전환부문 배출이 늘어난다"는 정책적으로 중요한 정보가 가려진다.

=== 전전화 시나리오의 설정

전전화 시나리오는 유형별 연간 전환율 $r^p_"conv/yr" (t_x, b)$의 경로로 정의한다.
구성 예시는 다음과 같다(구체적인 대상과 전환율은 연구진이 결정).

- X0: 전환 없음(기준)
- X1: 공공 건축물 우선 전환(그린리모델링 사업 대상 연계)
- X2: X1 + 민간 대규모 건물 전환
- X3: X2 + 주거(개별 난방 공동주택·단독주택) 확대

전체 시나리오 $p$는 신축 의무화 시나리오(기본, 강화①…⑤)와 전전화 시나리오(X0…X3)의 조합이다.
@eq:saving 이 두 효과의 합이므로, 각 효과를 따로 계산한 뒤 조합별로 더하면 된다.

=== 유의 사항

+ *KEEI REF에 이미 반영된 전기화 추세.*
  REF에서도 가정부문 전력 비중은 33.3%(2024)에서 37.4%(2050)로 늘고, 석유 수요는 줄어든다(`A1.6`).
  KEEI 보고서는 서비스부문 전망에 정부의 히트펌프 보급 활성화 정책 기조를 반영했다고 밝히고 있다(@section:ref).
  따라서 $r^p_"conv/yr" (t_x, b)$는 REF 추세를 *넘어서는 추가 전환율*로 정의하고, X0(추가 전환 없음)를 REF와 같게 둔다.
  REF에 반영된 히트펌프 보급 규모(대수, 대상 건물)는 공개되지 않았으므로 KEEI에 확인을 요청한다.
+ *연료 감소량의 상한.*
  전환으로 줄어드는 연료 배출은 REF의 해당 연료 배출을 넘을 수 없다.
  연료 $f in cal(F)_D$에 대해 다음을 매년 확인한다.
  $
    sum_(b in cal(B)_"new" (s)) r_"new" (t, b) k_"new" (t, b, f) R^p_"avg" (t, b, f) + sum_(b in cal(B)_"conv" (s)) r^p_"conv" (t, b) R^*_"conv" (t, b, f) >= -1
  $ <eq:conv-cap>
  REF의 연료 수요가 줄어드는 후반기(특히 등유·LPG)에는 이 조건을 넘기 쉽다.
  넘는 경우에는 전환 대상 재고 $A_"elig" (t, b)$나 전환율 $r^p_"conv/yr" (t_x, b)$를 다시 검토한다.
+ *설비 성능 저하.*
  히트펌프 성능 저하를 반영하려면 $e_"post" (b, "el")$에 연 $R_"deg,HP"$의 증가율을 적용한다.
  자료가 부족하면 기본값은 0으로 두고 민감도 분석에서 다룬다.
+ *태양광.*
  전전화와 함께 태양광을 설치하는 시나리오라면 @eq:pv 와 같은 방식으로 상쇄량을 별도로 계산한다.

= 계산 예시 (가상 수치)

2040년 비주거, 단일 유형, LNG(직접 배출)를 가정한다.

- $C_"REF" (2040, "nres", "LNG") = 15.0$ MtCO#sub[2]: LNG 수요 6.00 Mtoe에 배출계수를 곱한 값(가상)
- $r_"new" = 0.20$: 2025--2040년 신축이 재고의 20%
- $k_"new" = 0.8$: 신축 기준건물의 LNG 원단위가 재고 평균의 80%
- $R^p_"avg" = -0.15$: 시나리오 $p$의 평균 LNG 변화율

$ Delta C^p_"new" (2040, "nres", "LNG") = 15.0 times 0.20 times 0.8 times (-0.15) = -0.36 " MtCO"_2 $

다른 에너지원도 같은 방식으로 계산해 범위별로 합산한다.
B안에서는 전력의 $C_"REF" (2040, "nres", "el")$에 2040년 전력 배출계수(2024년 대비 0.49배)가 반영되어 있다.

*전전화 예시.*
2040년 주거, LNG 개별 난방 공동주택 한 유형의 전환을 가정한다.

- $C_"REF" (2040, "res", "LNG") = 20.0$ MtCO#sub[2], $C_"REF" (2040, "res", "el") = 40.0$ MtCO#sub[2] (가상)
- $r_"conv" = 0.05$: 2025--2040년 전환된 면적이 주거 재고의 5%
- $R^*_"conv" ("LNG") = -1.5$: 전환 대상 건물의 LNG 사용량이 재고 평균의 1.5배이고, 전량 전환($R_"conv" = -1$)
- $R^*_"conv" ("el") = +0.25$: 전력 증가량이 재고 평균 전력 원단위의 25%

$
  Delta C^p_"conv" (2040, "res", "LNG") & = 20.0 times 0.05 times (-1.5) = -1.50 " MtCO"_2 quad "(직접 배출 감소)" \
   Delta C^p_"conv" (2040, "res", "el") & = 40.0 times 0.05 times 0.25 = +0.50 " MtCO"_2 quad "(간접 배출 증가)"
$

순 변화량은 $-1.00$ MtCO#sub[2]로, 순 감축이다.
B안이라면 $C_"REF" (t, "res", "el")$이 연도에 따라 줄어들어 간접 배출 증가분도 함께 줄어든다.

= 기준 시나리오와의 관계(중복 계상 방지) <section:ref>

=== KEEI 보고서의 확인 결과

KEEI 보고서는 REF의 정책 가정을 다음과 같이 밝히고 있다(PDF 쪽수).

- REF는 현행 정책·지침·규제뿐 아니라 *시행이 예정된 정책 수단*의 도입을 반영한다(p\. ~5).
- REF는 2050년까지 현재 시행 중이거나 시행이 확정된 정책 기조가 유지된다는 가정의 기준선으로, IEA STEPS에 상응한다.
  선언적 목표나 확정되지 않은 기술은 제외했다(p\. ~11).
- 서비스부문 전망에는 그린리모델링 지원 사업의 지속, *제로에너지건축물 의무화 제도 확대*, 건물 자가발전 확대, *히트펌프 보급 활성화 정책 기조*가 반영되었다(p\. ~68).
- 가정부문은 '히트펌프 보급 활성화 방안'(2035년까지 350만 대)을 글상자로 소개하지만(p\. ~58), ZEB 의무화의 반영 여부는 명시하지 않았다.

따라서 *REF는 현행 ZEB 로드맵(기본 시나리오)을 포함하는 것으로 본다.*
가정부문은 명시적 언급이 없으나, 시행이 확정된 정책을 반영한다는 REF의 원칙에 따라 같게 취급한다.

=== 산정 방식

REF가 곧 기본 시나리오이므로, 강화 시나리오와 전전화 시나리오는 기본 시나리오와의 *차이*만 변화량으로 계산한다.

$ C^p (t, s, f) = C_"REF" (t, s, f) + [Delta C^p (t, s, f) - Delta C^"기본" (t, s, f)] $ <eq:main-ref>

- $Delta C^"기본" (t, s, f)$: 기본 시나리오(신축 의무화 기본, 전전화 X0)의 배출 변화량.
  전전화 X0는 REF 추세와 같으므로 $Delta C^"X0"_"conv" = 0$이다.
- 기본 시나리오의 감축 효과($Delta C^"기본"$, "ZEB 미적용" 대비)는 REF에 이미 포함된 것이므로 배출량에 다시 더하지 않고, 참고값으로 따로 보고한다.

#warning[
  KEEI는 REF에 반영한 ZEB 물량·등급 경로나 히트펌프 보급 규모를 공개하지 않았다.
  REF가 기본 시나리오의 일부만 반영했다면, @eq:main-ref 는 강화 시나리오의 효과를 *과소평가*한다.
  따라서 KEEI에 정책 반영 가정(ZEB 의무화 대상·시기, 히트펌프 보급 대수)의 확인을 요청한다.
  확인 결과 REF가 현행 로드맵을 포함하지 않는다면 @eq:main 을 그대로 적용한다.
]

= 결과 지표

+ 연도별 배출 변화량 $Delta C^p_D (t, s)$ (MtCO#sub[2], 음수 = 감축): 범위별($D$, $I$, $"PV"$)·에너지원별·부문별
+ 2025--2050년 누적 배출 변화량
+ 동일 연도 REF(= 기본 시나리오) 대비 변화율: $sum_f [Delta C^p (t, s, f) - Delta C^"기본" (t, s, f)] \/ sum_f C_"REF" (t, s, f)$
+ 정책적 해석
  - 직접 배출 감축은 NDC 건물부문 기여분이다.
  - 간접 배출 감축과 신재생 상쇄는 전환부문 기여분이다.
  - A안(ECO2 고정 계수) 결과는 인증 제도 기준의 감축 잠재량이다.
    국가 인벤토리 기준의 감축량과 비교할 때는 B안 결과를 함께 제시한다.
  - 전전화 효과는 직접 배출 감소, 간접 배출 증가, 순 효과를 나누어 제시한다.
+ 효과 구분: 신축 ZEB 의무화 효과($Delta C^p_"new"$)와 기존 건물 전전화 효과($Delta C^p_"conv"$)를 따로 제시한 뒤 합산한다.

#info[
  작년의 "2018년 대비 감축률"은 REF 자체의 배출 감소와 정책 효과가 섞이는 지표다.
  따라서 동일 연도 REF 대비 변화율을 주 지표로 권장한다.
]

= 민감도 분석 대상

- 배출계수 방안: A안(ECO2 고정) 대 B안(국가 계수 + 전망).
  B안 안에서는 KEEI REF 대 전력수급기본계획·NDC 전원 구성
- 신축 연면적: 10개년 평균(채택) 대 KEEI 재고 연동(@table:alt 의 ①, ②), 평균 기간(5·10·15년)
- 멸실률 $r_"demo"$, 허가–준공 시차 $n_"lag"$, 성능 저하율 $R_"deg"$($-0.05$--0)
- 실사용 보정계수 $k_"use"$(ECO2 평가값과 실사용량의 차이)
- 신축 건물 연료 구성: 시뮬레이션 고정값과 전기화 가정 비교
- 연도 보간 방법: PCHIP 대 선형(@table:interp-compare 기준으로 영향이 작을 것으로 예상)
- 전전화: 연간 전환율 $r^p_"conv/yr" (t_x, b)$, 히트펌프 성능(SCOP)과 성능 저하율 $R_"deg,HP"$,
  전환 전 사용량 보정계수 $k_"use,conv"$, 전환 대상 재고 $A_"elig" (2024, b)$

#heading(numbering: none)[
  부록.
  상향식 산정과의 등가성
]

@eq:saving-new 의 각 항을 정의대로 대입하면 다음과 같다(에너지원 $f$, 유형 $b$, 단일 준공연도, 인자 생략).

$
  C_"REF" dot r_"new" dot k_"new" dot R^p_"avg"
  = (Q dot "EF") dot (A_"new") / A_"stock" dot (e_"base") / (Q \/ A_"stock") dot R^p_"avg"
  = A_"new" dot e_"base" dot "EF" dot R^p_"avg"
$ <eq:equiv>

즉, 곱셈식은 "신축 면적 × 기준건물 원단위 × 배출계수 × 변화율"로 계산하는 상향식 배출 변화량과 *수학적으로 같다*.
KEEI 수요 $Q$는 약분된다.

전전화 효과(@eq:saving-x)도 같다.
$A^p_"conv,cum" (t, b)$를 생존한 누적 전환 면적(@eq:eta 의 분자)이라 하면 다음과 같다.

$
  C_"REF" dot r^p_"conv" dot R^*_"conv"
  = (Q dot "EF") dot (A^p_"conv,cum") / A_"stock" dot (e_"post" - e_"pre") / (Q \/ A_"stock")
  = A^p_"conv,cum" dot (e_"post" - e_"pre") dot "EF"
$ <eq:equiv-x>

즉 "전환 면적 × 에너지원별 사용량 변화 × 배출계수"의 상향식 계산과 같다.

결과적으로 $C_"REF"$는 (i) 변화율(%)의 분모, (ii) 배출계수(B안에서는 연도별 변화 포함)를 전달하는 역할만 한다.
재고 연면적 $A_"stock"$도 같은 이유로 약분된다.
채택안(A안 고정 배출계수, 10개년 평균 신축 물량)에서는 배출 변화량의 크기가 KEEI 전망과 무관하다(@section:decisions).

따라서 다음 두 가지에 유의해야 한다.

+ $k_"new"$를 $C_"REF"$와 *같은 KEEI 자료*로 계산해야 등가성이 성립한다.
  $k_"new"$를 생략하거나($k_"new" = 1$) 다른 출처의 재고 원단위를 쓰면, 배출 변화량이 KEEI 재고 평균 원단위에 비례해 왜곡된다.
+ 곱셈식으로 바꿔도 필요한 입력(신축 면적, 기준 원단위, 변화율)은 줄지 않는다.
  곱셈식의 장점은 *표현과 검증이 쉽다*는 데 있다.
