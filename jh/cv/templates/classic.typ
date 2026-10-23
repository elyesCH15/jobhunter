// Classic — one column, centred header, serif body, uppercase section headings with a rule.
// ATS-safe: real text only, no tables / columns / icons. All CV text arrives as string literals.

#let cv-setup(lang: "en", title: "", author: "", body) = {
  set document(title: title, author: author)
  set page(paper: "a4", margin: (x: 1.75cm, top: 1.4cm, bottom: 1.3cm))
  set text(font: ("Libertinus Serif", "Carlito"), size: 10.5pt, lang: lang, hyphenate: false)
  set par(justify: false, leading: 0.5em, spacing: 0.55em)
  set list(indent: 0.2em, body-indent: 0.5em, spacing: 0.42em, marker: [•])
  show link: set text(fill: rgb("#1a3d6d"))
  show heading.where(level: 1): it => block(above: 0.95em, below: 0.45em, width: 100%, sticky: true)[
    #set text(size: 11pt, weight: "bold", tracking: 0.06em)
    #upper(it.body)
    #v(-0.65em)
    #line(length: 100%, stroke: 0.5pt + rgb("#555555"))
  ]
  body
}

#let cv-header(name, headline, contacts) = align(center)[
  #block(below: 0.35em, text(size: 21pt, weight: "bold", name))
  #if headline != "" { block(below: 0.4em, text(size: 11.5pt, headline)) }
  #text(size: 9.5pt, contacts.map(c => box(if c.at(1) == none { c.at(0) } else { link(c.at(1), c.at(0)) })).join([ | ]))
]

#let cv-section(title) = heading(level: 1, title)

#let cv-para(body) = block(above: 0.3em, below: 0.2em, par(justify: true, body))

#let cv-entry(title: "", org: "", location: "", dates: "", url: "") = block(above: 0.75em, below: 0.4em, sticky: true)[
  #text(weight: "bold", title)#if org != "" [ — #org]#if location != "" [, #location]#if dates != "" [#h(1fr)#text(style: "italic", dates)]
  #if url != "" [ \ #text(size: 9.5pt, link("https://" + url, url))]
]

#let cv-line(text-body) = block(above: 0.3em, below: 0.2em, text-body)

#let cv-bullets(items) = list(..items)

#let cv-kv(label, value) = block(above: 0.42em, below: 0.42em)[#text(weight: "bold", label + ":") #value]
