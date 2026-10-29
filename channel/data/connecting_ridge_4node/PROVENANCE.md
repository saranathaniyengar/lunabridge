# Provenance

`expected_links.csv` and `scenario.json` were copied unchanged from
`lunar-colosseum-oai` (`scenarios/out/connecting_ridge_4node/`, commit
10b066a).
They are the output of `lunaremu build scenarios/connecting_ridge_4node.toml`
(lunar-channel-emulation): PGDA LOLA 5 m/px Site01 (Connecting Ridge), 2.5 GHz,
analytic two-ray + Deygout (ITU-R P.526) propagation with the Siegler et al.
(2020) regolith model. This is the input of `analysis/fig_channel.py`
(paper figure `lunar_channel`). It is NOT the Sionna RT channel used in the
live closed loop (see `channel/sionna_rt/`).
