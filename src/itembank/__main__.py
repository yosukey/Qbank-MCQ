"""CLI エントリ。

実装計画 §0 の方針「**CLI を常に併走させる**」に対応する。各機能はまず CLI
サブコマンドとして動かす。自動テストが書きやすく、GUI 不調時の回避手段にもなる。

    itembank db init | db migrate | db backup
    itembank inspect-docx FILE            # スパイク①: 全 run の書式ダンプ
    itembank import-exam --docx X --stats Y [--dry-run]
    itembank import-stats --exam ID --csv Y [--dry-run]
    itembank exams | bank | audit-sets
    itembank select --total 50 [--type A=30 --type X2=15]
    itembank finalize --exam ID
    itembank export --exam ID --what key|booklet|crosswalk|report|all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from . import __version__
from .core import paths
from .core.bank import (
    create_question_from_printed,
    find_duplicate_question,
    upsert_choice_set,
)
from .core.choiceset import audit_tagless_duplicates
from .core.db import ChoiceSet, Exam, make_session_factory
from .core.exam import (
    apply_stats,
    booklet_sources,
    build_candidates,
    check_finalize,
    create_exam,
    exam_summary,
    finalize_exam,
    flagged_after_import,
    selection_context,
    set_exam_items,
)
from .core.migrate import backup_database, open_database, read_schema_version
from .core.reporting import crosswalk_rows, report_rows
from .core.selection import SelectionConditions, select_candidates
from .core.validate import (
    ExamLimits,
    ParsedQuestionView,
    cross_validate_import,
    validate_stats_import,
)
from .io.csv_key import answer_key_filename, rows_from_exam_items, write_answer_key
from .io.csv_stats import StatsFormatError, parse_stats_csv
from .io.docx_read import dump_runs, parse_docx, summarize_formats
from .io.docx_write import BookletItem, write_booklet
from .io.xlsx_report import write_crosswalk, write_stats_report

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2


def _print_issues(issues: Sequence, *, prefix: str = "") -> tuple[int, int]:
    """検証結果を人が読める形で出す。``(ブロック数, 警告数)`` を返す。"""
    blockers = [i for i in issues if i.blocking]
    warnings = [i for i in issues if not i.blocking]
    for issue in blockers:
        print(f"{prefix}[ブロック] {issue.message}", file=sys.stderr)
    for issue in warnings:
        print(f"{prefix}[警告]     {issue.message}", file=sys.stderr)
    return len(blockers), len(warnings)


def _json_default(obj: object) -> object:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def _dump_json(data: object, path: Path | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"書き出しました: {path}")
    else:
        print(text)


def _open(args: argparse.Namespace) -> tuple[Session, object]:
    engine, result = open_database(Path(args.db) if args.db else None)
    if result.changed:
        print(f"スキーマを {result.from_version} → {result.to_version} に移行しました")
        if result.backup:
            print(f"バックアップ: {result.backup}")
    return make_session_factory(engine)(), engine


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------


def cmd_db(args: argparse.Namespace) -> int:
    db_file = Path(args.db) if args.db else paths.db_path()

    if args.db_action == "backup":
        dest = backup_database(db_file)
        print(f"バックアップ: {dest}" if dest else "DB がまだありません")
        return EXIT_OK

    engine, result = open_database(db_file)
    print(f"DB: {db_file}")
    print(f"スキーマ版: {read_schema_version(engine)}")
    if result.applied:
        print(f"適用した移行: {result.applied}")
        if result.backup:
            print(f"バックアップ: {result.backup}")
    elif args.db_action == "migrate":
        print("移行はありません(最新です)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# スパイク①: docx の書式ダンプ(実装計画 §12-3)
# ---------------------------------------------------------------------------


def cmd_inspect_docx(args: argparse.Namespace) -> int:
    dumps = dump_runs(args.file)
    summary = summarize_formats(dumps)

    if args.json is not None or args.raw:
        _dump_json(
            {"summary": summary, "runs": [d.as_dict() for d in dumps]},
            Path(args.json) if args.json else None,
        )
        return EXIT_OK

    print(f"run 総数: {len(dumps)}")
    print("--- 書式の内訳 ---")
    for key, count in summary.items():
        print(f"{count:6d}  {key}")

    unexpected = [d for d in dumps if d.unexpected]
    print(f"--- 想定外の書式を含む run: {len(unexpected)} 件 ---")
    for d in unexpected[:40]:
        print(f"  p{d.paragraph:4d} {d.text[:30]!r}: {', '.join(d.unexpected)}")
    if len(unexpected) > 40:
        print(f"  ... 他 {len(unexpected) - 40} 件")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 局面A: 過去問一括取込(設計書 §1.1)
# ---------------------------------------------------------------------------


def cmd_import_exam(args: argparse.Namespace) -> int:
    parsed = parse_docx(args.docx, images_dir=paths.images_dir())
    print(f"docx: {len(parsed.questions)} 設問を抽出しました")
    if parsed.skipped:
        print(f"  読み飛ばした体裁行: {len(parsed.skipped)} 行")
    if parsed.unexpected_formats:
        print(f"  想定外の書式: {len(parsed.unexpected_formats)} 箇所")

    stats_file = None
    if args.stats:
        try:
            stats_file = parse_stats_csv(args.stats)
        except StatsFormatError as exc:
            print(f"[ブロック] {exc}", file=sys.stderr)
            return EXIT_VALIDATION
        print(
            f"集計CSV[{stats_file.dialect}]: 選択式 {stats_file.n_rows} 行"
            f"、受験者数 {stats_file.meta.effective_n}"
        )
        if stats_file.non_mcq_rows:
            positions = ", ".join(str(r.position) for r in stats_file.non_mcq_rows)
            print(f"  選択式でない設問 {len(stats_file.non_mcq_rows)} 問を除外: 問{positions}")

    issues = list(parsed.issues)
    if stats_file is not None:
        views = [
            ParsedQuestionView(q.number, q.stem_html, tuple(q.choice_htmls))
            for q in parsed.questions
        ]
        issues.extend(cross_validate_import(views, stats_file.rows))

    n_block, n_warn = _print_issues(issues)
    print(f"相互検証: 不整合 {n_block} 件、警告 {n_warn} 件")

    if args.dry_run:
        _dump_json(
            {
                "document": parsed.as_dict(),
                "stats_rows": [
                    {
                        "position": r.position,
                        "correct": r.correct,
                        "p_reported": r.p_reported,
                        "n_correct": r.n_correct_reported,
                        "disc": r.disc,
                        "total": r.total,
                    }
                    for r in (stats_file.rows if stats_file else [])
                ],
                "issues": [
                    {"code": i.code, "message": i.message, "blocking": i.blocking} for i in issues
                ],
            },
            Path(args.json) if args.json else None,
        )
        return EXIT_VALIDATION if n_block else EXIT_OK

    if n_block and not args.force:
        print("不整合があるため取り込みませんでした(--force で強行できます)", file=sys.stderr)
        return EXIT_VALIDATION

    session, _ = _open(args)
    with session:
        code = _do_import_exam(session, args, parsed, stats_file)
        session.commit()
    return code


def _do_import_exam(session: Session, args, parsed, stats_file) -> int:
    correct_by_position = {r.position: r.correct for r in stats_file.rows} if stats_file else {}
    meta = stats_file.meta if stats_file else None

    exam = create_exam(
        session,
        name=args.name or (meta.exam_name if meta else Path(args.docx).stem),
        exam_date=args.date or (meta.exam_date if meta else None),
        course=args.course,
        cohort=args.cohort,
    )

    assignments: list[tuple[int, int]] = []
    duplicates = 0
    for q in parsed.questions:
        correct = correct_by_position.get(q.number)
        if not correct:
            print(f"問{q.number}: 正答が分からないため下書きとして登録します", file=sys.stderr)

        normalized = [c for c in q.choice_htmls]
        probe, _ = upsert_choice_set(session, normalized)
        existing = find_duplicate_question(session, q.stem_html, probe.id)
        if existing is not None:
            # 設計書 §1.4: 署名一致・高類似なら警告して統合を促す。
            duplicates += 1
            print(
                f"問{q.number}: 同じセット・同じ設問文の問題 {existing.id} が既にあります。"
                "統合を検討してください",
                file=sys.stderr,
            )

        result, _ = create_question_from_printed(
            session,
            stem_html=q.stem_html,
            printed_choices=normalized,
            correct=correct or "a",
            status="active" if correct else "draft",
            image_path=q.image_paths[0] if q.image_paths else None,
        )
        if result.blocked:
            _print_issues(result.issues, prefix=f"問{q.number}: ")
            print(f"問{q.number} を登録できませんでした", file=sys.stderr)
            continue
        assignments.append((q.number, result.version.id))

    set_exam_items(session, exam, assignments)
    report = finalize_exam(session, exam)
    if report.blocked:
        _print_issues(report.issues)
        print("finalize できなかったため統計は取り込みません", file=sys.stderr)
        return EXIT_VALIDATION

    print(f"試験 {exam.id}「{exam.name}」に {len(assignments)} 問を登録しました")
    if duplicates:
        print(f"重複の疑い: {duplicates} 件")

    if stats_file is not None:
        exam_items = {i.position: i.correct_asked for i in exam.items}
        issues = validate_stats_import(
            stats_file.rows,
            exam_items,
            pattern_columns_found=stats_file.pattern_columns_found,
            missing_fixed_columns=stats_file.missing_fixed_columns,
            n_examinees=stats_file.meta.n_examinees,
            n_non_mcq=len(stats_file.non_mcq_rows),
        )
        n_block, _ = _print_issues(issues)
        if n_block and not args.force:
            print("統計の検証に失敗したため取り込みませんでした", file=sys.stderr)
            return EXIT_VALIDATION
        result = apply_stats(
            session,
            exam,
            stats_file.rows,
            source_file=str(args.stats),
            disc_type=stats_file.meta.disc_type,
            n_examinees=stats_file.meta.effective_n,
        )
        print(f"統計を {result.written} 問に取り込みました")
        _print_flags(session, exam)
    return EXIT_OK


def _print_flags(session: Session, exam: Exam) -> None:
    """取込完了時にフラグの付いた問題を一覧表示する(設計書 §9.3)。"""
    flagged = flagged_after_import(session, exam)
    if not flagged:
        print("フラグの付いた問題はありません")
        return
    print(f"--- 要点検 {len(flagged)} 問(設計書 §9.3)---")
    for position, question_id, flags in flagged:
        print(f"  問{position:3d} (問題ID {question_id}): {', '.join(flags)}")


# ---------------------------------------------------------------------------
# 局面B: 統計取込(設計書 §9)
# ---------------------------------------------------------------------------


def cmd_import_stats(args: argparse.Namespace) -> int:
    try:
        stats_file = parse_stats_csv(args.csv)
    except StatsFormatError as exc:
        print(f"[ブロック] {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    session, _ = _open(args)
    with session:
        exam = session.get(Exam, args.exam)
        if exam is None:
            print(f"試験 {args.exam} がありません", file=sys.stderr)
            return EXIT_USAGE
        # 設計書 §9.1: 取込画面ではまず試験を選ぶ。finalized のものにのみ与えられる。
        if exam.status == "draft":
            print(
                f"試験 {exam.id} はまだ確定していません。先に finalize してください(設計書 §9.1)",
                file=sys.stderr,
            )
            return EXIT_USAGE

        exam_items = {i.position: i.correct_asked for i in exam.items}
        issues = validate_stats_import(
            stats_file.rows,
            exam_items,
            pattern_columns_found=stats_file.pattern_columns_found,
            missing_fixed_columns=stats_file.missing_fixed_columns,
            n_examinees=stats_file.meta.n_examinees,
            n_non_mcq=len(stats_file.non_mcq_rows),
        )
        n_block, n_warn = _print_issues(issues)
        print(f"検証チェーン: ブロック {n_block} 件、警告 {n_warn} 件")

        if args.dry_run:
            return EXIT_VALIDATION if n_block else EXIT_OK
        if n_block:
            print("検証に失敗したため取り込みませんでした", file=sys.stderr)
            return EXIT_VALIDATION

        result = apply_stats(
            session,
            exam,
            stats_file.rows,
            source_file=str(args.csv),
            disc_type=stats_file.meta.disc_type,
            n_examinees=stats_file.meta.effective_n,
        )
        print(f"統計を {result.written} 問に取り込みました(status={exam.status})")
        _print_flags(session, exam)
        session.commit()
    return EXIT_OK


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


def cmd_exams(args: argparse.Namespace) -> int:
    session, _ = _open(args)
    with session:
        exams = session.query(Exam).order_by(Exam.exam_date, Exam.id).all()
        if not exams:
            print("試験がまだありません")
            return EXIT_OK
        print(f"{'ID':>4} {'日付':<12} {'状態':<10} {'問数':>4} {'受験者':>6}  名称")
        for exam in exams:
            s = exam_summary(session, exam)
            print(
                f"{s['id']:>4} {str(s['exam_date'] or ''):<12} {s['status']:<10} "
                f"{s['n_items']:>4} {str(s['n_examinees'] or ''):>6}  {s['name'] or ''}"
            )
    return EXIT_OK


def cmd_bank(args: argparse.Namespace) -> int:
    session, _ = _open(args)
    with session:
        candidates = build_candidates(session)
        print(f"問題 {len(candidates)} 件")
        for c in candidates:
            if args.type and c.item_type != args.type:
                continue
            flags = f" [{','.join(sorted(c.flags))}]" if c.flags else ""
            print(f"  #{c.question_id:<4} v{c.qversion_id:<4} {c.label()}{flags}")
    return EXIT_OK


def cmd_audit_sets(args: argparse.Namespace) -> int:
    """タグ除去一致の監査(設計書 §6.2)。"""
    session, _ = _open(args)
    with session:
        sets = {cs.id: cs.item_htmls() for cs in session.query(ChoiceSet).all()}
        dupes = audit_tagless_duplicates(sets)
        if not dupes:
            print("タグ除去で一致する項目はありません")
            return EXIT_OK
        print(f"マークアップ違いの疑い: {len(dupes)} 件")
        for plain, set_ids in sorted(dupes.items()):
            print(f"  {plain!r}: セット {set_ids}")
    return EXIT_VALIDATION


# ---------------------------------------------------------------------------
# 出題支援(設計書 §13)
# ---------------------------------------------------------------------------


def _parse_pairs(values: Sequence[str] | None) -> dict[str, int] | None:
    if not values:
        return None
    out: dict[str, int] = {}
    for value in values:
        key, _, count = value.partition("=")
        out[key.strip()] = int(count)
    return out


def cmd_select(args: argparse.Namespace) -> int:
    session, _ = _open(args)
    with session:
        candidates = build_candidates(session)
        families, links = selection_context(session, candidates)
        conditions = SelectionConditions(
            total=args.total,
            type_distribution=_parse_pairs(args.type),
            tag_distribution=_parse_pairs(args.tag),
            min_disc=args.min_disc,
            exclude_recent_years=args.exclude_recent_years,
            current_year=args.year,
            new_item_ratio=args.new_ratio,
            p_range=(args.p_min, args.p_max) if args.p_min is not None else None,
            limits=ExamLimits(
                max_per_set_group=args.max_per_set,
                max_negative=args.max_negative,
            ),
        )
        result = select_candidates(
            candidates, conditions, derivation_families=families, set_links=links
        )

        print(f"候補 {len(candidates)} 件から {len(result.selected)} 問を選定しました")
        for item_type, group in sorted(result.by_type.items()):
            print(f"--- {item_type}: {len(group)} 問 ---")
            for c in group:
                print(f"  #{c.question_id:<4} {c.label()}")
        for message in result.unmet:
            print(f"[警告] {message}", file=sys.stderr)

        if args.create_exam:
            exam = create_exam(session, name=args.create_exam, exam_date=args.date)
            set_exam_items(
                session,
                exam,
                [(i + 1, c.qversion_id) for i, c in enumerate(result.selected)],
            )
            session.commit()
            print(f"試験 {exam.id}「{exam.name}」を作成しました(status=draft)")
    return EXIT_OK if not result.unmet else EXIT_VALIDATION


def cmd_finalize(args: argparse.Namespace) -> int:
    session, _ = _open(args)
    with session:
        exam = session.get(Exam, args.exam)
        if exam is None:
            print(f"試験 {args.exam} がありません", file=sys.stderr)
            return EXIT_USAGE

        limits = ExamLimits(max_per_set_group=args.max_per_set, max_negative=args.max_negative)
        if args.check_only:
            report = check_finalize(session, exam, limits=limits)
            _print_issues(report.issues)
            print("確定できます" if not report.blocked else "確定できません")
            return EXIT_VALIDATION if report.blocked else EXIT_OK

        report = finalize_exam(session, exam, limits=limits)
        _print_issues(report.issues)
        if report.blocked:
            print("確定できませんでした", file=sys.stderr)
            return EXIT_VALIDATION
        session.commit()
        print(f"試験 {exam.id} を確定しました(status={exam.status})")
    return EXIT_OK


def cmd_gui(args: argparse.Namespace) -> int:
    """GUI を起こす(設計書 §14)。PySide6 が無ければ導入方法を出して終える。"""
    from .app import main as gui_main

    return gui_main([sys.argv[0]], db_file=Path(args.db) if args.db else None)


def cmd_export(args: argparse.Namespace) -> int:
    session, _ = _open(args)
    out_dir = Path(args.out) if args.out else paths.exports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    with session:
        exam = session.get(Exam, args.exam)
        if exam is None:
            print(f"試験 {args.exam} がありません", file=sys.stderr)
            return EXIT_USAGE

        wanted = {"key", "booklet", "crosswalk", "report"} if args.what == "all" else {args.what}

        if "key" in wanted:
            pairs = [(i.position, i.correct_asked) for i in exam.items]
            path = write_answer_key(
                rows_from_exam_items(pairs), out_dir / answer_key_filename(exam.id)
            )
            print(f"正答キー: {path}")

        if "booklet" in wanted:
            items = [
                BookletItem(
                    position=s.position,
                    stem_html=s.stem_html,
                    choices=s.choices,
                    image_path=s.image_path,
                    render_overrides=s.render_overrides,
                )
                for s in booklet_sources(session, exam)
            ]
            path = write_booklet(
                items, out_dir / f"booklet_{exam.id}.docx", title=exam.name or None
            )
            print(f"問題冊子: {path}")

        if "crosswalk" in wanted:
            path = write_crosswalk(
                crosswalk_rows(session, exam),
                out_dir / f"crosswalk_{exam.id}.xlsx",
                exam_name=exam.name,
            )
            print(f"教員用照合表: {path}")

        if "report" in wanted:
            rows = report_rows(session, exam)
            stems = {s.position: s.stem_html for s in booklet_sources(session, exam)}
            path = write_stats_report(
                rows, out_dir / f"report_{exam.id}.xlsx", exam_name=exam.name, stem_texts=stems
            )
            print(f"統計レポート: {path}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 引数
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="itembank", description="口腔組織学 試験問題バンク")
    parser.add_argument("--version", action="version", version=f"itembank {__version__}")
    parser.add_argument("--db", help="SQLite ファイル(既定は %%APPDATA%%\\ItemBank)")
    parser.add_argument("-v", "--verbose", action="store_true", help="デバッグログを出す")
    sub = parser.add_subparsers(dest="command", required=True)

    p_db = sub.add_parser("db", help="DB の作成・移行・バックアップ")
    p_db.add_argument("db_action", choices=["init", "migrate", "backup"])
    p_db.set_defaults(func=cmd_db)

    p_inspect = sub.add_parser("inspect-docx", help="全 run の書式をダンプする(スパイク①)")
    p_inspect.add_argument("file")
    p_inspect.add_argument("--json", nargs="?", const="", help="JSON で出す(パス省略で標準出力)")
    p_inspect.add_argument("--raw", action="store_true", help="集計せず JSON を標準出力へ")
    p_inspect.set_defaults(func=cmd_inspect_docx)

    p_import = sub.add_parser("import-exam", help="過去問一括取込(局面A)")
    p_import.add_argument("--docx", required=True)
    p_import.add_argument("--stats", help="集計 CSV")
    p_import.add_argument("--dry-run", action="store_true", help="登録せず結果を JSON で出す")
    p_import.add_argument("--json", help="--dry-run の出力先")
    p_import.add_argument("--name")
    p_import.add_argument("--date")
    p_import.add_argument("--course")
    p_import.add_argument("--cohort")
    p_import.add_argument("--force", action="store_true", help="不整合があっても取り込む")
    p_import.set_defaults(func=cmd_import_exam)

    p_stats = sub.add_parser("import-stats", help="確定済みの試験に統計を与える(局面B)")
    p_stats.add_argument("--exam", type=int, required=True)
    p_stats.add_argument("--csv", required=True)
    p_stats.add_argument("--dry-run", action="store_true")
    p_stats.set_defaults(func=cmd_import_stats)

    p_exams = sub.add_parser("exams", help="試験の一覧")
    p_exams.set_defaults(func=cmd_exams)

    p_bank = sub.add_parser("bank", help="問題の一覧")
    p_bank.add_argument("--type", help="タイプで絞る (A/X2/X3/X4/XX)")
    p_bank.set_defaults(func=cmd_bank)

    p_audit = sub.add_parser("audit-sets", help="タグ除去一致の監査(設計書 §6.2)")
    p_audit.set_defaults(func=cmd_audit_sets)

    p_select = sub.add_parser("select", help="出題候補の選定(設計書 §13.1)")
    p_select.add_argument("--total", type=int, required=True)
    p_select.add_argument("--type", action="append", metavar="A=30", help="タイプ別配分")
    p_select.add_argument("--tag", action="append", metavar="発生=10", help="分野別配分")
    p_select.add_argument("--min-disc", type=float)
    p_select.add_argument("--exclude-recent-years", type=int)
    p_select.add_argument("--year", type=int, help="今年(直近 n 年の判定に使う)")
    p_select.add_argument("--new-ratio", type=float, help="新作の混入率 (0.0〜1.0)")
    p_select.add_argument("--p-min", type=float)
    p_select.add_argument("--p-max", type=float, default=1.0)
    p_select.add_argument("--max-per-set", type=int, default=2)
    p_select.add_argument("--max-negative", type=int)
    p_select.add_argument("--create-exam", metavar="名称", help="選定結果で試験を作る")
    p_select.add_argument("--date")
    p_select.set_defaults(func=cmd_select)

    p_final = sub.add_parser("finalize", help="finalize 前チェックと確定(設計書 §13.3)")
    p_final.add_argument("--exam", type=int, required=True)
    p_final.add_argument("--check-only", action="store_true")
    p_final.add_argument("--max-per-set", type=int, default=2)
    p_final.add_argument("--max-negative", type=int)
    p_final.set_defaults(func=cmd_finalize)

    p_export = sub.add_parser("export", help="出力物の書き出し(設計書 §13.2)")
    p_export.add_argument("--exam", type=int, required=True)
    p_export.add_argument(
        "--what", choices=["key", "booklet", "crosswalk", "report", "all"], default="all"
    )
    p_export.add_argument("--out", help="出力先ディレクトリ")
    p_export.set_defaults(func=cmd_export)

    p_gui = sub.add_parser("gui", help="画面を開く(設計書 §14)")
    p_gui.set_defaults(func=cmd_gui)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths.setup_logging(logging.DEBUG if args.verbose else logging.WARNING)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:
        log.exception("処理に失敗しました")
        print(f"エラー: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
