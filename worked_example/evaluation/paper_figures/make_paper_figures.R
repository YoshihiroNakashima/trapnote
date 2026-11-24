# ==============================================================================
# 論文用の図を作る（TrapNote と、SpeciesNet の再学習の評価）
#
# 数字のもと（この台本の中には、結果の数字を書いていない。全部、次のファイルから読む）
#   <base_dir>/paper_tables/論文用の表_<stamp>_通しの指標.csv   paper_table.py が作る表（通しの再現率・適合率・F1・正答率と、95% 信頼区間）
#   <base_dir>/results_<stamp>/matrix_<gold>_<モデル>.csv       compare_gold.py が作る混同行列（種の分類に進んだ動画）
# 集計をやり直したら、下の stamp を新しい日時に変えて、流し直す。
#
# 作る図（<base_dir>/paper_figures/figures_<stamp>_<lang>/ に、PNG と PDF で出す）
#   fig0    TrapNote の利用の流れ（模式図。表の数字は使わない）
#   fig1    学習に用いた動画の本数と精度（動物の通しの再現率、macro F1、動物なしの通しの再現率、全動画での通しの正答率）
#   fig2    種ごとの通しの再現率（色の表。gold 2 つ × モデル 8 つ。gold の本数が 10 本より少ない種には色を塗らない）
#   fig3    少数種の gold：種ごとの通しの再現率
#   fig4    混同行列（元の SpeciesNet と、200本の微調整のモデル。調査回の gold。並べるモデルは conf_models で変える）
#   figS1   種ごとの適合率（fig2 と同じ形）
#   figS2   種ごとの F1（fig2 と同じ形）
#   figS3   軽量な分類器で、判定のときに元の SpeciesNet の確率を混ぜる割合（0.3 と 0）
#   figS4   混同行列（全部のモデル、gold 3 つ。PDF の 1 ページに 1 つ）
# 図の読み方: 点は値、点を通る縦の線は 95% 信頼区間（地点を単位としたブートストラップ。表の「幅の下」「幅の上」）。
#   横の破線は、元の SpeciesNet（17区分に絞る）の値。本文の図に出すモデルは、原稿の表 1 と同じ 8 つ。
#
# 使い方
#   RStudio で開いて「Source」を押す。または、コマンドで  Rscript make_paper_figures.R [ja|en] [出す先のフォルダ]
#   R 4.2 より古い Windows の R では、source("make_paper_figures.R", encoding = "UTF-8") で流す。
# 要るパッケージ: ggplot2, dplyr, tidyr, readr（install.packages("tidyverse") でまとめて入る）。
#   ragg が入っていれば、PNG はそれで描く（日本語の文字がきれいに出る）。なくても動く。
# ==============================================================================

# ---- 0. 設定（変えるのは、ここだけ） -------------------------------------------
base_dir    <- "I:/Copy/evaluation"              # 評価のフォルダ（paper_tables と results_<日時> がある所）。Rscript で流すときは、台本の場所から決め直す（下）
stamp       <- "20261113_115626"                 # 集計の日時（表とフォルダの名前に付いているもの）
lang        <- "ja"                              # 図の中の言葉: "ja"（日本語）か "en"（英語）
rule_use    <- "アプリの規則"                    # 数え方（もう 1 つは "線を使わない数え方"）
formats     <- c("png", "pdf")                   # 出す形
conf_gold   <- "gold_season"                     # fig4 の gold（"gold_season" か "gold_station"）
conf_models <- c("SpeciesNet（絞る）", "1 回目") # fig4 に並べるモデル（表の「モデル」の列の名前）
out_dir     <- NULL                              # NULL なら <base_dir>/paper_figures/figures_<stamp>_<lang>
fig_width   <- 170                               # 図の幅（mm）
min_n_fill  <- 10                                # 種ごとの色の表で、gold の本数がこれより少ない種には色を塗らない（値は灰色の字で出す）

args <- commandArgs(trailingOnly = TRUE)
# Rscript で流したときは、評価のフォルダを、この台本の場所（<評価のフォルダ>/paper_figures/）から決める（ドライブの文字が PC によって違うため）
.script <- sub("^--file=", "", grep("^--file=", commandArgs(trailingOnly = FALSE), value = TRUE))
if (length(.script) == 1 && file.exists(.script)) {
  .base <- dirname(dirname(normalizePath(.script, winslash = "/")))
  if (dir.exists(file.path(.base, "paper_tables"))) base_dir <- .base
}
if (length(args) >= 1 && args[1] %in% c("ja", "en")) lang <- args[1]
if (length(args) >= 2) out_dir <- args[2]
if (is.null(out_dir)) out_dir <- file.path(base_dir, "paper_figures", paste0("figures_", stamp, "_", lang))
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

# ---- 1. パッケージ --------------------------------------------------------------
suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(readr)
})
if (utils::packageVersion("ggplot2") < "3.4.0") stop("ggplot2 が古いです。install.packages(\"ggplot2\") で 3.4 以上にしてください。")

# ---- 2. モデルの一覧と、図の中の言葉 --------------------------------------------
# model は、表の「モデル」の列の名前。method は方式、stage は区分あたりの最大の本数（0 = 学習なし）
systems <- tibble::tribble(
  ~model,                        ~method,   ~stage,
  "SpeciesNet（そのまま）",      "open",    "0",
  "SpeciesNet（絞る）",          "base",    "0",
  "First_200 だけ",              "ft_pre",  "200",
  "1 回目",                      "ft",      "200",
  "2 回目",                      "ft",      "700",
  "3 回目",                      "ft",      "1200",
  "軽い方式（混ぜない）1 回目",  "light",   "200",
  "軽い方式（混ぜない）2 回目",  "light",   "700",
  "軽い方式（混ぜない）3 回目",  "light",   "1200",
  "軽い方式 1 回目",             "light03", "200",
  "軽い方式 2 回目",             "light03", "700",
  "軽い方式 3 回目",             "light03", "1200"
)
stage_levels <- c("0", "200", "700", "1200")
main_methods <- c("base", "ft_pre", "ft", "light")      # 本文の図に出す方式（原稿の表 1 と同じ 8 つのモデル）
main_golds   <- c("gold_season", "gold_station")

TXT <- list(
  ja = list(
    gold    = c(gold_season = "調査回の gold", gold_station = "地点の gold", gold_species = "少数種の gold"),
    gold2   = c(gold_season = "調査回の\ngold", gold_station = "地点の\ngold"),
    method  = c(open = "元の SpeciesNet（絞らない）", base = "元の SpeciesNet", ft_pre = "微調整（動物なしを加える前）", ft = "微調整",
                light = "軽量な分類器", light03 = "軽量な分類器（元の確率を 0.3 の割合で混ぜる）"),
    base_full = "元の SpeciesNet（17区分に絞る）", light_nomix = "軽量な分類器（混ぜない）",
    group   = c(base = "元の\nSpeciesNet", ft = "微調整", light = "軽量な分類器"),
    stage   = c("0" = "元の\nSpeciesNet", "200" = "200本", "700" = "700本", "1200" = "1,200本"),
    base_col = "17区分に\n絞る", pre_col = "動物なしを\n加える前",
    xlab    = "学習に用いた動画（区分あたりの最大の本数）",
    metric  = c(animal_recall = "動物の\n通しの再現率（%）", macro_f1 = "macro F1", empty_recall = "動物なしの\n通しの再現率（%）",
                all_acc = "全動画での\n通しの正答率（%）", tanuki_recall = "タヌキの\n通しの再現率（%）"),
    heat    = c("通しの再現率" = "通しの再現率（%）", "適合率" = "適合率（%）", "F1" = "F1"),
    recall_y = "通しの再現率（%）",
    empty = "動物なし", review = "要確認", n_videos = "%s本", name_n = "%s（%s）", classes = "%s区分の平均",
    human = "人の判定（gold）", ai = "AI の判定", row_pct = "行の中の割合（%）", full_sep = "　",
    wf_title = c(load = "1　読み込み", auto = "2　自動判定", check = "3　目視の確認", export = "4　書き出し", train = "再学習"),
    wf_body  = c(load = "フォルダを指定する\n地点と調査回は\nフォルダの並びから決まる",
                 auto = "検出：MegaDetector\n種の提案：SpeciesNet、\nまたは再学習したモデル",
                 check = "動画を再生して種を確定する\n確信度の低い動画や\n判定の食い違う動画に絞れる",
                 export = "CSV\n（占有モデル、\n密度推定など）",
                 train = "軽量な分類器（GPU を使わず、数分）\nまたは SpeciesNet 本体の微調整"),
    wf_note  = c(checked = "確認した動画", model = "再学習したモデル", loop = "調査のたびに、自動判定、目視の確認、\n再学習を繰り返す")
  ),
  en = list(
    gold    = c(gold_season = "Held-out survey set", gold_station = "Held-out station set", gold_species = "Rare-species set"),
    gold2   = c(gold_season = "Held-out\nsurvey\nset", gold_station = "Held-out\nstation\nset"),
    method  = c(open = "Original SpeciesNet (all labels)", base = "Original SpeciesNet", ft_pre = "Fine-tuning (before\nadding empty videos)", ft = "Fine-tuning",
                light = "Lightweight classifier", light03 = "Lightweight classifier (mixing weight 0.3)"),
    base_full = "Original SpeciesNet (restricted to 17 classes)", light_nomix = "Lightweight classifier (no mixing)",
    group   = c(base = "Original\nSpeciesNet", ft = "Fine-tuning", light = "Lightweight classifier"),
    stage   = c("0" = "Original\nSpeciesNet", "200" = "200", "700" = "700", "1200" = "1,200"),
    base_col = "17 classes", pre_col = "200, no\nempty videos",
    xlab    = "Training videos (maximum per class)",
    metric  = c(animal_recall = "End-to-end recall,\nanimals (%)", macro_f1 = "Macro F1", empty_recall = "End-to-end recall,\nempty videos (%)",
                all_acc = "End-to-end accuracy,\nall videos (%)", tanuki_recall = "End-to-end recall,\nraccoon dog (%)"),
    heat    = c("通しの再現率" = "End-to-end recall (%)", "適合率" = "Precision (%)", "F1" = "F1"),
    recall_y = "End-to-end recall (%)",
    empty = "Empty", review = "Needs review", n_videos = "n = %s", name_n = "%s (%s)", classes = "mean of %s classes",
    human = "Human (test set)", ai = "AI", row_pct = "Share of the row (%)", full_sep = ", ",
    wf_title = c(load = "1  Import", auto = "2  Automated classification", check = "3  Manual verification", export = "4  Export", train = "Retraining"),
    wf_body  = c(load = "Choose a folder;\nstations and surveys\nare read from the folders",
                 auto = "Detection: MegaDetector\nSpecies: SpeciesNet or\na retrained model",
                 check = "Play each video and\nconfirm the species; filter to\nlow-confidence or\nconflicting videos",
                 export = "CSV\n(occupancy, density\nestimation, etc.)",
                 train = "Lightweight classifier (no GPU, minutes)\nor fine-tuning of SpeciesNet"),
    wf_note  = c(checked = "Verified videos", model = "Retrained model", loop = "Repeat classification, verification\nand retraining after every survey")
  )
)
tx <- TXT[[lang]]

col_method <- c(open = "grey55", base = "grey20", ft_pre = "#0072B2", ft = "#0072B2", light = "#D55E00", light03 = "#D55E00")
shp_method <- c(open = 0, base = 15, ft_pre = 1, ft = 16, light = 17, light03 = 2)
x_off      <- c(open = 0, base = 0, ft_pre = -0.27, ft = -0.09, light = 0.09, light03 = 0.27)   # 同じ本数の点を、横に少しずらす
pal_seq    <- c("#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6")               # 低い = 薄い、高い = 濃い
pal_pos    <- c(0, 0.5, 0.7, 0.8, 0.9, 1)                                                       # 高いほうを細かく塗り分ける

# ---- 3. 文字（日本語のフォント）と、保存のしかた --------------------------------
pick_font <- function() {
  if (lang != "ja") return("")
  cand <- if (.Platform$OS.type == "windows") c("Yu Gothic", "Meiryo", "MS Gothic")
          else if (Sys.info()[["sysname"]] == "Darwin") c("Hiragino Sans", "Hiragino Kaku Gothic ProN")
          else c("Noto Sans CJK JP", "IPAexGothic", "TakaoGothic")
  if (requireNamespace("systemfonts", quietly = TRUE)) {
    hit <- cand[cand %in% unique(systemfonts::system_fonts()$family)]
    if (length(hit) > 0) return(hit[1])
  }
  cand[1]
}
font_family <- pick_font()
if (.Platform$OS.type == "windows" && nzchar(font_family)) {      # ragg を使わないときの PNG 用
  do.call(grDevices::windowsFonts, stats::setNames(list(grDevices::windowsFont(font_family)), font_family))
}
update_geom_defaults("text", list(family = font_family))

theme_paper <- function() {
  theme_bw(base_size = 8.5, base_family = font_family) +
    theme(panel.grid.minor = element_blank(), panel.grid.major.x = element_blank(),
          panel.grid.major.y = element_line(linewidth = 0.25, colour = "grey90"),
          strip.background = element_blank(), strip.text = element_text(face = "bold", size = 8.5),
          strip.placement = "outside", axis.text = element_text(colour = "black"),
          legend.key.height = unit(4, "mm"), legend.margin = margin(0, 0, 0, 0), legend.text = element_text(size = 8),
          plot.margin = margin(2, 3, 2, 2, "mm"))
}

written <- character(0)
save_fig <- function(p, name, height, width = fig_width) {
  for (f in formats) {
    file <- file.path(out_dir, paste0(name, "_", lang, ".", f))
    if (f == "png") {
      dev <- if (requireNamespace("ragg", quietly = TRUE)) ragg::agg_png else "png"
      ggsave(file, p, device = dev, width = width, height = height, units = "mm", dpi = 300, bg = "white")
    } else if (f == "pdf") {
      dev <- if (capabilities("cairo")) grDevices::cairo_pdf else "pdf"
      ggsave(file, p, device = dev, width = width, height = height, units = "mm")
    } else {
      ggsave(file, p, width = width, height = height, units = "mm")
    }
    written <<- c(written, file)
  }
  invisible(p)
}

# ---- 4. 表を読む ----------------------------------------------------------------
tab_file <- file.path(base_dir, "paper_tables", paste0("論文用の表_", stamp, "_通しの指標.csv"))
res_dir  <- file.path(base_dir, paste0("results_", stamp))
if (!file.exists(tab_file)) stop("表が見つかりません: ", tab_file)

d_all <- read_csv(tab_file, col_types = cols(.default = col_character()), show_col_types = FALSE)
stopifnot(ncol(d_all) == 13)
names(d_all) <- c("gold", "rule", "row", "en_name", "sci_name", "metric", "n", "k", "model", "model_id", "value", "lo", "hi")
d_all <- d_all %>% mutate(across(c(n, k, value, lo, hi), as.numeric))
d <- d_all %>% filter(rule == rule_use) %>% inner_join(systems, by = "model")
miss <- setdiff(systems$model, d$model)
if (length(miss) > 0) stop("表にないモデルがあります: ", paste(miss, collapse = "、"))

# 種の名前。並べる順は、種がいちばん多い gold での順（アプリの種リストの順）。英語の図では、表の「英名」を使う
sp_tab  <- d %>% filter(!is.na(en_name), en_name != "") %>% distinct(gold, row, en_name)
g_most  <- names(which.max(table(sp_tab$gold)))
species <- bind_rows(sp_tab %>% filter(gold == g_most), sp_tab %>% filter(gold != g_most)) %>% distinct(row, en_name)
sp_order <- c(species$row, "動物なし", "要確認")
# 表にない名前（17 区分の外。元の SpeciesNet の答えをそのまま数えた混同行列の列に出る）の英名。アプリの種リスト（resources/species_default.csv）の英名に合わせる
sp_extra <- c("ニホンカモシカ" = "Japanese Serow", "アカギツネ" = "Red Fox", "哺乳類（種不明）" = "Mammal")
sp_label <- function(x) {
  if (lang == "ja") return(x)
  x <- as.character(x)
  out <- species$en_name[match(x, species$row)]
  out[x == "動物なし"] <- tx$empty
  out[x == "要確認"]   <- tx$review
  hit <- is.na(out) & x %in% names(sp_extra)
  out[hit] <- unname(sp_extra[x[hit]])
  left <- unique(x[is.na(out)])
  if (length(left) > 0) warning("英名のない名前が、日本語のまま残ります: ", paste(left, collapse = "、"))
  ifelse(is.na(out), x, out)
}
num <- function(x) format(x, big.mark = ",", trim = TRUE)

# gold で確定した本数（種ごと）。どのモデルでも同じなので、元の SpeciesNet の行から取る
n_gold <- d %>% filter(metric == "通しの再現率", method == "base") %>% select(gold, row, n_gold = n)

# 図の中のモデルの名前（1 行で書くとき）
model_full <- function(method, stage) {
  ifelse(method == "base", tx$base_full,
         ifelse(method %in% c("open", "ft_pre"), tx$method[method], paste0(tx$method[method], tx$full_sep, tx$stage[stage])))
}

# 凡例を、図の中に置く（x, y は凡例の左端・上下の中央の位置。図の左下が 0,0、右上が 1,1）
legend_inside <- function(x, y) {
  pos <- if (utils::packageVersion("ggplot2") >= "3.5.0") theme(legend.position = "inside", legend.position.inside = c(x, y))
         else theme(legend.position = c(x, y))
  pos + theme(legend.justification = c(0, 0.5), legend.background = element_blank())
}

# ---- 5. 折れ線の図（fig1、fig3、figS3 で使う） ----------------------------------
# df の列: method, stage, value, lo, hi と、パネルを分ける列。base（元の SpeciesNet）の値には、横の破線も引く
# labels: 凡例の名前を変えるとき（方式の名前 → 表示）。legend_rows: 凡例の行の数
curve_plot <- function(df, facets, methods, ylab = NULL, ann = NULL, labels = NULL, legend_rows = 1) {
  lab <- tx$method[methods]
  if (!is.null(labels)) lab[names(labels)] <- labels
  df <- df %>% filter(method %in% methods) %>%
    mutate(x = match(stage, stage_levels) + unname(x_off[method]), method = factor(method, levels = methods))
  p <- ggplot(df, aes(x, value, colour = method, shape = method)) +
    geom_hline(data = df %>% filter(method == "base"), aes(yintercept = value), colour = "grey55", linetype = "22", linewidth = 0.3) +
    geom_line(data = df %>% filter(method %in% c("ft", "light", "light03")), aes(group = method), linewidth = 0.4) +
    geom_linerange(aes(ymin = lo, ymax = hi), linewidth = 0.4, na.rm = TRUE) +
    geom_point(size = 1.9, stroke = 0.6, fill = "white") +
    scale_x_continuous(breaks = seq_along(stage_levels), labels = unname(tx$stage[stage_levels]), limits = c(0.6, 4.4)) +
    scale_colour_manual(values = col_method[methods], breaks = methods, labels = unname(lab), name = NULL) +
    scale_shape_manual(values = shp_method[methods], breaks = methods, labels = unname(lab), name = NULL) +
    facets + labs(x = tx$xlab, y = ylab) + theme_paper() +
    theme(legend.position = "bottom") + guides(colour = guide_legend(nrow = legend_rows), shape = guide_legend(nrow = legend_rows))
  if (!is.null(ann)) {
    p <- p + geom_text(data = ann, aes(x = Inf, y = -Inf, label = lab), hjust = 1.08, vjust = -0.7, size = 2.3,
                       colour = "grey35", inherit.aes = FALSE)
  }
  p
}

# ---- fig0  TrapNote の利用の流れ（模式図。数字は使わない） ------------------------
# 箱の位置は mm（図の左下が 0,0）。kind: io = 入出力、ai = AI がする段階、human = 人がする段階
wf <- tibble::tribble(
  ~key,     ~xmin, ~xmax, ~ymin, ~ymax, ~kind,
  "load",       2,    34,    40,    72, "io",
  "auto",      41,    87,    40,    72, "ai",
  "check",     94,   132,    40,    72, "human",
  "export",   139,   168,    40,    72, "io",
  "train",     60,   118,     4,    30, "ai"
) %>% mutate(title = tx$wf_title[key], body = tx$wf_body[key])
wf_arrows <- tibble::tribble(
  ~x,  ~y, ~xend, ~yend,
  34,  56,    41,    56,
  87,  56,    94,    56,
  132, 56,   139,    56,
  106, 40,   106,    30,      # 目視の確認 → 再学習
  72,  30,    72,    40       # 再学習 → 自動判定
)
p0 <- ggplot() +
  geom_rect(data = wf, aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = kind), colour = "grey35", linewidth = 0.3) +
  geom_text(data = wf, aes(x = (xmin + xmax) / 2, y = ymax - 4.5, label = title), fontface = "bold", size = 3.0) +
  geom_text(data = wf, aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2 - 3.5, label = body), size = 2.55, lineheight = 1.05) +
  geom_segment(data = wf_arrows, aes(x = x, y = y, xend = xend, yend = yend), linewidth = 0.5, colour = "grey20",
               arrow = arrow(length = unit(1.8, "mm"), type = "closed")) +
  annotate("text", x = 108, y = 35, label = tx$wf_note["checked"], hjust = 0, size = 2.5, colour = "grey20") +
  annotate("text", x = 70, y = 35, label = tx$wf_note["model"], hjust = 1, size = 2.5, colour = "grey20") +
  annotate("text", x = 2, y = 17, label = tx$wf_note["loop"], hjust = 0, size = 2.6, colour = "grey30", lineheight = 1.05) +
  scale_fill_manual(values = c(io = "#f2f2f2", ai = "#deebf7", human = "#fde5cf"), guide = "none") +
  coord_fixed(xlim = c(0, 170), ylim = c(0, 76), expand = FALSE) +
  theme_void(base_family = font_family)
save_fig(p0, "fig0_workflow", height = 76)

# ---- fig1  学習に用いた動画の本数と精度 ------------------------------------------
pick_rows <- function(golds, row_regex, metric_name, key) {
  d %>% filter(gold %in% golds, grepl(row_regex, row), metric == metric_name) %>% mutate(key = key)
}
f1 <- bind_rows(
  pick_rows(main_golds, "^動物（全体）$",               "通しの再現率", "animal_recall"),
  pick_rows(main_golds, "^動物（[0-9]+ 種の平均）$",    "F1",           "macro_f1"),
  pick_rows(main_golds, "^動物なし$",                   "通しの再現率", "empty_recall"),
  pick_rows(main_golds, "^全動画（動物と動物なし）$",   "通しの正答率", "all_acc")
) %>% filter(method %in% main_methods)
stopifnot(nrow(f1) == 4 * length(main_golds) * sum(systems$method %in% main_methods))    # 指標 4 つ × gold × モデル 8 つが、1 行ずつある
f1 <- f1 %>% mutate(metric_lab = factor(tx$metric[key], levels = tx$metric[c("animal_recall", "macro_f1", "empty_recall", "all_acc")]),
                    gold_lab   = factor(tx$gold[gold], levels = tx$gold[main_golds]))
# パネルの隅に、動画の本数（macro F1 は、平均した区分の数）を書く
ann1 <- f1 %>% filter(method == "base") %>%
  mutate(lab = ifelse(key == "macro_f1", sprintf(tx$classes, sub("^動物（([0-9]+) 種の平均）$", "\\1", row)), sprintf(tx$n_videos, num(n)))) %>%
  select(metric_lab, gold_lab, lab)
p1 <- curve_plot(f1, facet_grid(metric_lab ~ gold_lab, scales = "free_y", switch = "y"), main_methods, ann = ann1)
save_fig(p1, "fig1_training_size_and_accuracy", height = 200)

# ---- fig3  少数種の gold：種ごとの通しの再現率 -----------------------------------
f3 <- d %>% filter(gold == "gold_species", metric == "通しの再現率", method %in% main_methods, n >= 10)   # 本数を決めて選んだ 5 種（ほかの種は 1〜2 本）
f3 <- f3 %>% mutate(sp_lab = sprintf(tx$name_n, sp_label(row), sprintf(tx$n_videos, num(n))),
                    sp_lab = factor(sp_lab, levels = unique(sp_lab[order(match(row, sp_order))])))
# 5 種を 3 つと 2 つの 2 段に並べ、空いた右下に凡例を置く
p3 <- curve_plot(f3, facet_wrap(~ sp_lab, nrow = 2), main_methods, ylab = tx$recall_y, legend_rows = length(main_methods)) +
  scale_y_continuous(limits = c(0, 100), breaks = seq(0, 100, 20), expand = expansion(mult = c(0.01, 0.03))) +
  legend_inside(0.70, 0.25)
save_fig(p3, "fig3_rare_species", height = 125)

# ---- figS3  軽量な分類器：元の SpeciesNet の確率を混ぜる割合（0.3 と 0） -----------
fs3 <- bind_rows(
  pick_rows(main_golds, "^動物（全体）$",             "通しの再現率", "animal_recall"),
  pick_rows(main_golds, "^タヌキ$",                   "通しの再現率", "tanuki_recall"),
  pick_rows(main_golds, "^動物なし$",                 "通しの再現率", "empty_recall"),
  pick_rows(main_golds, "^動物（[0-9]+ 種の平均）$",  "F1",           "macro_f1")
) %>% filter(method %in% c("base", "light03", "light")) %>%
  mutate(metric_lab = factor(tx$metric[key], levels = tx$metric[c("animal_recall", "tanuki_recall", "empty_recall", "macro_f1")]),
         gold_lab   = factor(tx$gold[gold], levels = tx$gold[main_golds]))
ps3 <- curve_plot(fs3, facet_grid(metric_lab ~ gold_lab, scales = "free_y", switch = "y"), c("base", "light03", "light"),
                  labels = c(light = tx$light_nomix))
save_fig(ps3, "figS3_lightweight_mixing", height = 200)

# ---- 6. 種ごとの色の表（fig2、figS1、figS2） -------------------------------------
heat_plot <- function(metric_name) {
  is_f1 <- metric_name == "F1"
  df <- d %>% filter(gold %in% main_golds, metric == metric_name, method %in% main_methods, row %in% c(species$row, "動物なし")) %>%
    left_join(n_gold, by = c("gold", "row")) %>%
    mutate(group_lab = factor(tx$group[ifelse(method == "ft_pre", "ft", method)], levels = tx$group),
           col_lab   = ifelse(method == "base", tx$base_col, ifelse(method == "ft_pre", tx$pre_col, tx$stage[stage])),
           col_lab   = factor(col_lab, levels = c(tx$base_col, tx$pre_col, tx$stage[stage_levels[-1]])),
           gold_lab  = factor(tx$gold2[gold], levels = tx$gold2[main_golds]),
           row_lab   = sprintf(tx$name_n, sp_label(row), num(n_gold)),
           small     = n_gold < min_n_fill,                                     # 本数が少ない種: 色を塗らず、灰色の字にする
           fill_v    = ifelse(small, NA, if (is_f1) value * 100 else value),
           txt_col   = ifelse(small | is.na(value), "grey50", "black"),
           lab       = ifelse(is.na(value), "–", if (is_f1) sprintf("%.2f", value) else sprintf("%.0f", value)))
  lev <- df %>% arrange(desc(match(row, sp_order)), gold) %>% pull(row_lab) %>% unique()      # 上から、種リストの順
  df$row_lab <- factor(df$row_lab, levels = lev)
  ggplot(df, aes(col_lab, row_lab, fill = fill_v)) +
    geom_tile(colour = "white", linewidth = 0.5) +
    geom_text(aes(label = lab, colour = txt_col), size = 2.4) +
    facet_grid(gold_lab ~ group_lab, scales = "free", space = "free") +
    scale_fill_gradientn(colours = pal_seq, values = pal_pos, limits = c(0, 100), na.value = "grey96",
                         name = tx$heat[[metric_name]], labels = if (is_f1) function(v) sprintf("%.2f", v / 100) else waiver()) +
    scale_colour_identity() +
    scale_x_discrete(expand = c(0, 0)) + scale_y_discrete(expand = c(0, 0)) +
    labs(x = tx$xlab, y = NULL) + theme_paper() +
    theme(panel.grid = element_blank(), panel.border = element_blank(), axis.ticks = element_blank(),
          strip.clip = "off", strip.text.y = element_text(angle = 0, hjust = 0),
          legend.position = "bottom", legend.key.width = unit(14, "mm"), legend.key.height = unit(2.5, "mm"),
          legend.title = element_text(size = 8, vjust = 0.9), panel.spacing.x = unit(2, "mm"))
}
save_fig(heat_plot("通しの再現率"), "fig2_species_recall", height = 185)
save_fig(heat_plot("適合率"),       "figS1_species_precision", height = 185)
save_fig(heat_plot("F1"),           "figS2_species_f1", height = 185)

# ---- 7. 混同行列（fig4、figS4） --------------------------------------------------
# 行 = 人の判定（gold）、列 = AI の判定。種の分類に進んだ動画だけ（検出で枠が付かなかった動画は入っていない）
model_file_id <- function(model) {
  id <- d_all$model_id[match(model, d_all$model)]
  ifelse(model == "SpeciesNet（絞る）", "SpeciesNet_closed", ifelse(model == "SpeciesNet（そのまま）", "SpeciesNet_open", id))
}
read_matrix <- function(gold, model) {
  file <- file.path(res_dir, sprintf("matrix_%s_%s.csv", gold, model_file_id(model)))
  if (!file.exists(file)) stop("混同行列が見つかりません: ", file)
  m <- read_csv(file, col_types = cols(.default = col_character()), show_col_types = FALSE)
  names(m)[1] <- "human"
  stopifnot(names(m)[ncol(m)] == "本数")
  names(m)[ncol(m)] <- "total"
  ai_levels <- setdiff(names(m), c("human", "total"))
  s <- systems[match(model, systems$model), ]
  m %>% pivot_longer(all_of(ai_levels), names_to = "ai", values_to = "count") %>%
    mutate(count = as.numeric(count), total = as.numeric(total), pct = 100 * count / total,
           model = model, gold = gold, panel = model_full(s$method, s$stage),
           ai_lab    = factor(sp_label(ai), levels = sp_label(ai_levels)),
           human_lab = factor(sprintf(tx$name_n, sp_label(human), num(total)),
                              levels = rev(unique(sprintf(tx$name_n, sp_label(human), num(total))))))
}
conf_plot <- function(gold, models, title = NULL) {
  df <- bind_rows(lapply(models, function(m) read_matrix(gold, m)))
  df$panel <- factor(df$panel, levels = unique(df$panel))
  ggplot(df, aes(ai_lab, human_lab, fill = pct)) +
    geom_tile(colour = "white", linewidth = 0.4) +
    geom_text(data = df %>% filter(count > 0), aes(label = count), size = 2.1) +
    facet_wrap(~ panel, ncol = 1, scales = "free") +
    scale_fill_gradientn(colours = c("white", pal_seq[-1]), values = c(0, 0.02, 0.2, 0.5, 0.8, 1), limits = c(0, 100), name = tx$row_pct) +
    scale_x_discrete(expand = c(0, 0)) + scale_y_discrete(expand = c(0, 0)) +
    labs(x = tx$ai, y = tx$human, title = title) + theme_paper() +
    theme(panel.grid = element_blank(), axis.ticks = element_blank(), axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1),
          panel.border = element_rect(colour = "grey60", linewidth = 0.3), legend.position = "right",
          legend.key.height = unit(10, "mm"), legend.key.width = unit(3, "mm"), legend.title = element_text(size = 8),
          plot.title = element_text(size = 9, face = "bold"))
}
save_fig(conf_plot(conf_gold, conf_models), paste0("fig4_confusion_", conf_gold), height = 110 * length(conf_models))

# figS4: 全部のモデルの混同行列（PDF の 1 ページに 1 つ）
if ("pdf" %in% formats) {
  file <- file.path(out_dir, paste0("figS4_confusion_all_models_", lang, ".pdf"))
  if (capabilities("cairo")) grDevices::cairo_pdf(file, width = fig_width / 25.4, height = 125 / 25.4, onefile = TRUE)
  else grDevices::pdf(file, width = fig_width / 25.4, height = 125 / 25.4, onefile = TRUE)
  for (g in c(main_golds, "gold_species")) for (m in systems$model) print(conf_plot(g, m, title = tx$gold[[g]]))
  invisible(dev.off())
  written <- c(written, file)
}

# ---- 8. おわり ------------------------------------------------------------------
cat("もとの表: ", tab_file, "\n", sep = "")
cat("数え方: ", rule_use, "   言葉: ", lang, "   フォント: ", if (nzchar(font_family)) font_family else "(既定)", "\n", sep = "")
cat("できた図（", length(written), "）:\n", paste0("  ", written, collapse = "\n"), "\n", sep = "")
