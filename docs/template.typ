#import "@preview/booktyps:0.1.1": booktabs

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
  show ref: set text(fill: rgb("#0069a8"))

  body
}
