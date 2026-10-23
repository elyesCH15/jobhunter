// Compact — one column, left-aligned header, sans body (Carlito, metric-compatible with Calibri),
// tighter spacing to keep a full early-career CV on one page.
// ATS-safe: real text only, no tables / columns / icons. All CV text arrives as string literals.

#let accent = rgb("#0f5f58")

#let cv-setup(lang: "en", title: "", author: "", body) = {
  set document(title: title, author: author)
  set page(paper: "a4", margin: (x: 1.35cm, top: 0.95cm, bottom: 0.8cm))
  set text(font: ("Carlito", "Libertinus Serif"), size: 10pt, lang: lang, hyphenate: false)
  set par(justify: false, leading: 0.42em, spacing: 0.42em)
  set list(indent: 0.1em, body-indent: 0.45em, spacing: 0.3em, marker: text(fill: accent)[•])
  show link: set text(fill: rgb("#1a3d6d"))
  show heading.where(level: 1): it => block(above: 0.72em, below: 0.38em, width: 100%, sticky: true)[
    #set text(size: 11pt, weight: "bold", fill: accent)
    #it.body
    #v(-0.7em)
    #line(length: 100%, stroke: 0.6pt + accent)
  ]
  body
}

#let cv-header(name, headline, contacts) = block(below: 0.2em)[
  #block(below: 0.3em)[#text(size: 19pt, weight: "bold", name)#if headline != "" [ #h(0.4em) #text(size: 11.5pt, fill: accent, headline)]]
  #text(size: 9.5pt, contacts.map(c => box(if c.at(1) == none { c.at(0) } else { link(c.at(1), c.at(0)) })).join([ | ]))
]

#let cv-section(title) = heading(level: 1, title)

#let cv-para(body) = block(above: 0.25em, below: 0.15em, body)

#let cv-entry(title: "", org: "", location: "", dates: "", url: "") = block(above: 0.55em, below: 0.3em, sticky: true)[
  #text(weight: "bold", title)#if org != "" [ — #org]#if location != "" [, #location]#if url != "" [ — #text(size: 9.5pt, link("https://" + url, url))]#if dates != "" [#h(1fr)#dates]
]

#let cv-line(text-body) = block(above: 0.25em, below: 0.15em, text-body)

#let cv-bullets(items) = list(..items)

#let cv-kv(label, value) = block(above: 0.24em, below: 0.24em)[#text(weight: "bold", label + ":") #value]
