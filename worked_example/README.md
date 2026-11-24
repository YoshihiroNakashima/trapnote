# Worked example: analysis code, evaluation lists and reproducibility scripts

This folder holds the material behind the worked example in the manuscript that describes TrapNote: the code that calculated the reported values, the lists of the evaluation videos with the label of each video and the output of each model, and the reproducibility scripts that TrapNote exported for the models.

The files are copies of the working files, unchanged. Comments in the scripts, and the column names and labels in the tables, are in Japanese. The glossary at the end of this page translates them.

## What is here

```
worked_example/
  evaluation/
    compare_gold.py                 compares the models on the test sets and writes results_<time stamp>/
    paper_table.py                  calculates the values and confidence intervals reported in the manuscript and writes paper_tables/
    paper_figures/
      make_paper_figures.R          draws the figures from paper_tables/ and results_<time stamp>/
    results_20261113_115626/        output of compare_gold.py that the manuscript uses
      videos_gold_season.csv        list of evaluation videos, held-out survey set (videos that reached species classification)
      videos_gold_station.csv       the same for the held-out station set
      videos_gold_species.csv       the same for the rare-species set
      matrix_<set>_<model>.csv      confusion matrices
      compare_*, top1_*, sites_*, detected_*, speciesnet_open_*, summary.md    summary tables
    paper_tables/                   output of paper_table.py that the manuscript uses
      論文用の表_20261113_115626_通しの指標.csv            end-to-end recall, precision, F1 and accuracy, with confidence intervals
      論文用の表_20261113_115626_正解率.csv                proportion correct among the videos that reached species classification
      論文用の表_20261113_115626_差.csv                    paired differences between models
      論文用の表_20261113_115626_種の判別に進まなかった動画.csv   list of evaluation videos that did not reach species classification
      論文用の表_20261113_115626.md                        all tables in one file
    holdout_mix_20261113/           analysis of the mixing weight of the lightweight classifier (Appendix S2), with its scripts, outputs and report (結果.md)
  reproducibility_scripts/          the eleven scripts exported from TrapNote (one for the automated classification, ten for the retrained models)
```

## What is not here

The videos, the TrapNote projects (which hold the verified labels, the stored detections and the features) and the weights of the retrained models are not in this folder. As a result:

- `make_paper_figures.R` runs with the files in this folder alone.
- `compare_gold.py`, `paper_table.py`, the scripts in `holdout_mix_20261113/` and the reproducibility scripts read the TrapNote projects. They are provided to show exactly how the values were calculated and how the models were trained. Their outputs are included, so the values in the manuscript can be checked against the lists of videos without the projects.
- The scripts contain the drive letters and folders of the computers on which they were run (for example `F:/Copy/...`).

## Test sets and models

| Name in the files | Name in the manuscript |
|---|---|
| `gold_season` | Held-out survey set |
| `gold_station` | Held-out station set |
| `gold_species` | Rare-species set |

| Name in the tables | Model ID | Model in the manuscript |
|---|---|---|
| SpeciesNet（絞る） | `SpeciesNet_closed` | Original SpeciesNet, restricted to the 17 classes |
| SpeciesNet（そのまま） | `SpeciesNet_open` | Original SpeciesNet, with candidates restricted only to the species list of the project |
| First_200 だけ | `B_20261109_1554` | Fine-tuning, before empty videos were added |
| 1 回目 | `B_20261110_1911` | 200-video model (fine-tuning) |
| 2 回目 | `B_20261111_1543` | 700-video model (fine-tuning) |
| 3 回目 | `B_20261111_2231` | 1,200-video model (fine-tuning) |
| 軽い方式（混ぜない）1 回目, 2 回目, 3 回目 | `A_20261113_1148`, `A_20261113_1149`, `A_20261113_1150` | 200-, 700- and 1,200-video lightweight classifiers, without mixing in the original SpeciesNet probabilities (the values reported in the manuscript) |
| 軽い方式 1 回目, 2 回目, 3 回目 | `A_20261112_2255`, `A_20261113_0251`, `A_20261113_0255` | The same classifiers with the original SpeciesNet probabilities mixed in at a weight of 0.3 (Appendix S2) |

In TrapNote, method A is the lightweight classifier and method B is fine-tuning.

## How the values in the manuscript were obtained

1. TrapNote classified the videos of the test sets with each model (the original SpeciesNet and the retrained models).
2. `compare_gold.py` read the labels and the model outputs from the projects of the three test sets and wrote `results_20261113_115626/`. The files `videos_<set>.csv` give one row per video.
3. `paper_table.py` read these outputs and wrote `paper_tables/`. It counts a classification as correct when the first species given by the application is among the species determined for the video. Confidence intervals come from a bootstrap over stations (20,000 resamples, with a fixed seed for each row). Before writing, the script checks that the counts recalculated from the per-video rows agree with the summary tables of step 2.
4. `make_paper_figures.R` drew the figures from `paper_tables/` and from the confusion matrices in `results_20261113_115626/`.

Two counting rules appear in the tables. `アプリの規則` (rule of the application) is the rule that TrapNote applies and the one used in the manuscript: a video is marked 'needs review' when no species reaches the probability threshold, and 'empty' is given only when the mean 'empty' probability reaches its threshold. `線を使わない数え方` (counting without thresholds) takes the class with the highest mean probability and never withholds an answer. The columns that start with `線なし` in `videos_<set>.csv` give the answers under the second rule.

## Lists of evaluation videos

The evaluation videos of a test set are the rows of `videos_<set>.csv` with `対象` = 1 together with the rows for that set in `論文用の表_20261113_115626_種の判別に進まなかった動画.csv`. For the held-out survey set this gives 3,557 + 1,322 = 4,879 videos, and for the held-out station set 2,211 + 707 = 2,918 videos, as in Appendix S1. Rows with `対象` = 0 are listed but not counted: they are videos in which the species could not be determined (the label contains 不明, unknown), videos with only people or vehicles, and videos without a label.

Columns of `videos_<set>.csv`: `item_id` (number of the video in the project), `rel_path` (path of the video below the root folder of the archive: project, survey, station and file), `調査回` (survey), `地点` (station), `人の判定` (species determined by manual verification), then one column per model with the answer of that model.

## Drawing the figures again

R with ggplot2 (3.4 or later), dplyr, tidyr and readr is required.

```
Rscript worked_example/evaluation/paper_figures/make_paper_figures.R en
```

The figures are written to `worked_example/evaluation/paper_figures/figures_20261113_115626_en/` as PNG and PDF. Without `en`, the labels are in Japanese.

The script was run on Linux (R 4.5.2, ggplot2 4.0.2) with the files in this folder alone. The text in the figures was the same as in the figures of the manuscript, which were drawn on Windows, with one exception. In `figS2_species_f1`, the F1 of the Japanese marten on the held-out station set with the 200-video fine-tuned model is 0.765, and this value on a rounding boundary is printed as 0.76 on Windows and as 0.77 on Linux.

| File | Figure in the manuscript |
|---|---|
| `fig0_workflow` | Figure 1 |
| `fig1_training_size_and_accuracy` | Figure 2 |
| `fig2_species_recall` | Figure 3 |
| `fig3_rare_species` | Figure S1 |
| `fig4_confusion_gold_season` | Figure S2 |
| `figS1_species_precision` | Figure S3 |
| `figS2_species_f1` | Figure S4 |
| `figS3_lightweight_mixing` | Figure S5 |
| `figS4_confusion_all_models` | Confusion matrices of all models (separate PDF of the Supporting Information) |

## Reproducibility scripts

Each script in `reproducibility_scripts/` was exported from TrapNote. It records the settings of detection and classification, the settings of retraining, the projects that supplied the training material and the result obtained when the model was trained, and it re-runs these steps without the graphical interface. The verified labels stored in the project are the input, so the scripts need the projects and the videos.

The scripts were exported by the build of 23 November 2026, which reports version 0.3.0. That build differs from version 0.4.0 only in the version number. The scripts use the TrapNote that is installed when they are run and do not compare version numbers.

## Glossary

Classes:

| Japanese | English | Scientific name |
|---|---|---|
| イノシシ | Wild boar | *Sus scrofa* |
| ニホンジカ | Sika deer | *Cervus nippon* |
| キョン | Reeves' muntjac | *Muntiacus reevesi* |
| タヌキ | Raccoon dog | *Nyctereutes procyonoides* |
| アライグマ | Northern raccoon | *Procyon lotor* |
| ハクビシン | Masked palm civet | *Paguma larvata* |
| ニホンアナグマ | Japanese badger | *Meles anakuma* |
| ホンドテン | Japanese marten | *Martes melampus* |
| ニホンイタチ | Japanese weasel | *Mustela itatsi* |
| ネコ | Domestic cat | *Felis catus* |
| イヌ | Domestic dog | *Canis familiaris* |
| ニホンザル | Japanese macaque | *Macaca fuscata* |
| ニホンノウサギ | Japanese hare | *Lepus brachyurus* |
| ニホンリス | Japanese squirrel | *Sciurus lis* |
| 齧歯目（種不明） | Rodent (species not determined) | Rodentia |
| 鳥類（種不明） | Bird (species not determined) | Aves |
| 動物なし | Empty | |

Other answers and labels: 要確認 = needs review, 不明 = unknown, 人 = person, 車 = vehicle, 哺乳類（種不明） = mammal (species not determined), アカギツネ = red fox, ニホンカモシカ = Japanese serow. Two names joined by `+` mean that two species were determined for one video.

Column names and row labels:

| Japanese | English |
|---|---|
| 調査回, 地点 | Survey, station |
| 人の判定 | Species determined by manual verification |
| 人 \ AI | Manual verification (rows) against automated classification (columns) |
| 対象 | 1 if the video is counted, 0 if it is listed but not counted |
| アプリの答え | Answer of the application |
| 本数, 地点数 | Number of videos, number of stations |
| 正解, 答えなし | Correct, no answer ('needs review' or unknown) |
| 動物なしと答えた | Answered 'empty' |
| 数え方 | Counting rule (アプリの規則 = rule of the application, 線を使わない数え方 = counting without thresholds) |
| 行, 行（対象の動画） | Row (class or group of videos) |
| 英名, 学名 | English name, scientific name |
| 指標 | Metric |
| 通しの再現率, 適合率, 通しの正答率 | End-to-end recall, precision, end-to-end accuracy |
| …の差（ポイント） | Difference in percentage points |
| 本数（分母）, 数えた本数（分子） | Denominator, numerator |
| モデル, モデルの名前 | Name of the model in the tables, model ID |
| 値 | Value (recall, precision and accuracy in %, F1 from 0 to 1) |
| 幅の下, 幅の上 | Lower and upper limits of the 95% confidence interval (bootstrap over stations) |
| Wilson の下, Wilson の上 | Lower and upper limits of the Wilson interval |
| 数えたもの | What was counted |
| 動物の動画に「動物なし」 | Animal videos answered 'empty' |
| 動物なしの動画に種の答え | Empty videos given a species |
| 動物の動画で答えなし, 動物なしの動画で答えなし | Animal videos without an answer, empty videos without an answer |
| 動物, 動物なし, 合計 | Videos with animals, empty videos, total |
| 動物（全体） | All videos with animals |
| 全動画（動物と動物なし） | All videos (with animals and empty) |
| 動物（15 種の平均）, 動物（16 種の平均） | Mean across the animal classes (macro average) |
| 動物（学習に使った地点）, 動物（それ以外の地点） | Videos with animals at stations used for training, at other stations |
| 種：… | Class … |
| 不正解 → 正解（本）, 正解 → 不正解（本） | Number of videos that changed from incorrect to correct, from correct to incorrect |
| 学習したモデル | Retrained model |
| 一致, 違う | Agree, differ |
| 上位の分類で答えた | Answered with a higher taxon that contains the species |
| 確認済みの本数 | Number of verified videos |
| 枠が付いて種を判別した本数 | Number of videos with a box that reached species classification |
