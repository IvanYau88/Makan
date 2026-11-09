# Menu page fixtures

Hand-made pages in the shapes restaurant sites use, for `makan.places.menu_extract` and the menu fetch tests.
They are not copies of any real restaurant's page, and every venue, dish and price in them is invented.

| File | Shape |
| --- | --- |
| `json_ld.html` | A site builder's `Restaurant` JSON-LD with `hasMenu`, sections and `MenuItem` offers, plus text written to steer a model |
| `microdata.html` | schema.org `MenuItem` microdata with nested `Offer` prices |
| `html_list.html` | No structured data: a list, a table, and name-then-price blocks, with navigation, a footer, hidden text, a promotion and fees to ignore |
| `injection.html` | A heuristic menu whose lines try to instruct a model, hide text, or carry markup and links |
| `no_menu.html` | A home page with an about section, opening hours and one promotion, and no dish list |
