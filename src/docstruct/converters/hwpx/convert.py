"""HWP → HWPX 변환 어댑터.

역할:
    pyhwp(AGPL) 대신 HWPX 경로를 쓰려면 .hwp 를 .hwpx 로 바꿔야 한다.
    쓸 만한 변환기는 모두 외부 프로세스(Java 등)이므로, 그것을 호출하고
    없을 때 조용히 물러나는 일을 여기서 맡는다.
호출부:
    docstruct.extractors.hwp (변환기가 설정된 경우에만)
입력: .hwp 파일 경로
출력: 변환된 .hwpx 임시 파일 경로

왜 어댑터로 분리하는가
--------------------
변환기 후보가 여럿이고(hwp2hwpx / hwpConverter / 상용 도구) 어느 것을 쓸지
아직 정하지 못했다. 호출 규약만 고정해 두면 도구가 바뀌어도 파이프라인은
그대로다. **변환기가 없으면 None 을 돌려주고 기존 경로가 계속 쓰인다** —
설치 여부가 동작을 깨뜨리지 않아야 한다.

설정
----
    DOCSTRUCT_HWP2HWPX          변환 명령. `{input}` `{output}` 자리표시자 사용
    DOCSTRUCT_HWP2HWPX_TIMEOUT  제한 시간(초). 기본 300
    DOCSTRUCT_HWP2HWPX_DIR      설치 폴더 (기본: /opt/hwp2hwpx, Colab 은 /content)

설치 도우미
----------
    use_converter("/opt/hwp2hwpx")   이미 받아 둔 jar 로 연결 (사내 서버·도커)
    install_converter()              Maven Central 에서 내려받아 설치
    check_converter(sample="a.hwp")  실제 파일로 동작 확인

`use_converter` 가 사내 기본 경로다. Maven Central 이 막힌 망이 많고,
도커라면 jar 를 이미지에 넣어 두는 편이 낫기 때문이다.

측정된 비용 (894KB · 72쪽 · 표 212 문서 기준)
-------------------------------------------
변환은 세 부분으로 나뉘며 우리 환경에서 각각 실측했다.

    HWP 레코드 순회(읽기)   2.4초   (이벤트 95,096개)
    XML 생성·파싱 7.8MB     0.4초
    zip 재작성(쓰기)        0.1초
    ─────────────────────────
    순수 I/O·파싱 합계      2.8초

여기에 모델 변환 비용과 JVM 기동(~0.5초)이 더해지므로 **문서당 3~6초**로
본다. 참고로 같은 문서의 PDF 경로는 236초였다.

즉 변환은 **동기 요청 안에서 감당 가능한 수준**이지만, 배치에서는 문서
수만큼 곱해진다. 60개 부처면 3~6분이다.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

#: 변환 명령 템플릿을 담는 환경변수.
CONVERTER_ENV = "DOCSTRUCT_HWP2HWPX"
#: 제한 시간 환경변수.
TIMEOUT_ENV = "DOCSTRUCT_HWP2HWPX_TIMEOUT"
#: 기본 제한 시간(초). 큰 문서도 넘기도록 넉넉히 잡는다.
DEFAULT_TIMEOUT = 300.0


class ConversionUnavailable(RuntimeError):
    """변환기가 설정되지 않았거나 실행할 수 없다."""


def converter_command() -> str | None:
    """설정된 변환 명령 템플릿.

    입력: 없음 (`DOCSTRUCT_HWP2HWPX`)
    출력: 명령 문자열. 미설정이면 None
    """
    value = os.environ.get(CONVERTER_ENV, "").strip()
    return value or None


def is_available() -> bool:
    """변환을 시도할 수 있는지.

    입력: 없음
    출력: 명령이 설정되어 있고 실행파일이 존재하면 True
    비고:
        실행파일 존재까지 확인한다. 명령만 설정하고 설치를 안 한 상태를
        "가능" 으로 보면, 문서마다 실패하고 나서야 알게 된다.
    """
    command = converter_command()
    if not command:
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        _log.warning("%s 값을 명령으로 해석하지 못했습니다: %r", CONVERTER_ENV, command)
        return False
    if not argv:
        return False
    import shutil

    return shutil.which(argv[0]) is not None


def timeout_seconds() -> float:
    """변환 제한 시간.

    입력: 없음 (`DOCSTRUCT_HWP2HWPX_TIMEOUT`)
    출력: 초 단위 실수. 잘못된 값이면 기본값
    """
    raw = os.environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r — 기본 %s초를 씁니다",
                     TIMEOUT_ENV, raw, DEFAULT_TIMEOUT)
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def convert(hwp_path: str | Path, out_dir: str | Path | None = None) -> Path:
    """HWP 를 HWPX 로 변환한다.

    입력:
        hwp_path  원본 .hwp 경로
        out_dir   결과를 둘 폴더. 생략하면 임시 폴더
    출력: 생성된 .hwpx 경로
    예외:
        ConversionUnavailable  변환기 미설정·미설치
        RuntimeError           변환 실패 (종료코드 ≠ 0 또는 결과 파일 없음)
        TimeoutError           제한 시간 초과
    동작:
        명령 템플릿의 `{input}`·`{output}` 을 실제 경로로 채워 실행한다.
        결과 파일이 만들어졌고 비어 있지 않은지까지 확인한다 — 종료코드가
        0 이어도 빈 파일을 남기는 도구가 있다.
    """
    command = converter_command()
    if not command:
        raise ConversionUnavailable(
            f"{CONVERTER_ENV} 가 설정되지 않았습니다. "
            f'예: {CONVERTER_ENV}="java -jar /opt/hwp2hwpx.jar {{input}} {{output}}"'
        )

    source = Path(hwp_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"원본을 찾을 수 없습니다: {source}")

    target_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="hwp2hwpx-"))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}.hwpx"

    argv = [
        part.replace("{input}", str(source)).replace("{output}", str(target))
        for part in shlex.split(command)
    ]

    _log.info("HWP → HWPX 변환 중: %s", source.name)
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout_seconds(),
        )
    except FileNotFoundError as exc:
        raise ConversionUnavailable(f"변환기를 실행할 수 없습니다: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"{source.name}: 변환이 {timeout_seconds():.0f}초를 넘겼습니다"
        ) from exc

    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").splitlines()[-10:])
        raise RuntimeError(
            f"변환 실패 (종료코드 {result.returncode})\n{tail or '(표준 오류 없음)'}"
        )
    # 종료코드가 0 이어도 결과가 없을 수 있다. 실제로 그런 도구를 겪었다.
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"변환기가 결과 파일을 만들지 않았습니다: {target}")

    _log.info("변환 완료: %s (%.0f KB)", target.name, target.stat().st_size / 1024)
    return target


def try_convert(hwp_path: str | Path, out_dir: str | Path | None = None) -> Path | None:
    """변환을 시도하되 실패해도 예외를 내지 않는다.

    입력: hwp_path — 원본 경로, out_dir — 결과 폴더
    출력: 변환된 .hwpx 경로. 변환기가 없거나 실패하면 None
    비고:
        호출부가 "되면 HWPX 경로, 안 되면 기존 경로" 로 쓰기 위한 형태다.
        변환기 설치 여부가 파이프라인을 깨뜨리지 않아야 한다.
    """
    if not is_available():
        return None
    try:
        return convert(hwp_path, out_dir)
    except Exception as exc:                     # noqa: BLE001 - 폴백이 있으므로 삼킨다
        _log.warning("HWP → HWPX 변환에 실패해 기존 경로를 씁니다: %s", exc)
        return None


# ── 설치 도우미 ─────────────────────────────────────────────────────

#: hwp2hwpx 와 그것이 필요로 하는 jar 들 (Maven Central).
#: fat jar 가 아니므로 hwplib·hwpxlib 도 함께 받아야 한다.
HWP2HWPX_JARS = {
    "hwp2hwpx": "kr/dogfoot/hwp2hwpx/1.0.3/hwp2hwpx-1.0.3.jar",
    "hwplib": "kr/dogfoot/hwplib/1.1.10/hwplib-1.1.10.jar",
    "hwpxlib": "kr/dogfoot/hwpxlib/1.0.6/hwpxlib-1.0.6.jar",
}

#: Maven Central 기본 주소.
MAVEN_BASE = "https://repo1.maven.org/maven2"

#: jar 와 실행 스크립트를 둘 폴더.
#: Colab 이면 /content, 그 밖에는 /opt 를 기본으로 삼는다.
#: `DOCSTRUCT_HWP2HWPX_DIR` 로 바꿀 수 있다.
DEFAULT_INSTALL_DIR = Path(
    os.environ.get("DOCSTRUCT_HWP2HWPX_DIR")
    or ("/content/hwp2hwpx" if Path("/content").is_dir() else "/opt/hwp2hwpx")
)


def install_converter(dest: str | Path = DEFAULT_INSTALL_DIR, *, verbose: bool = True) -> Path:
    """HWP → HWPX 변환기를 설치한다.

    입력: dest — 설치 폴더, verbose — 진행 상황 출력
    출력: 설치된 폴더 Path
    예외: Java 가 없거나 jar 를 받지 못하면 RuntimeError
    동작:
        ① JDK 확인 (없으면 apt 로 설치) ② Maven Central 에서 jar 3개 내려받기
        ③ 실행 스크립트 생성 ④ `DOCSTRUCT_HWP2HWPX` 환경변수 설정.

        환경변수(`DOCSTRUCT_HWP2HWPX`)는 **프로세스 안에서만** 유지된다.
        서버를 재시작하거나 Colab 런타임을 다시 시작하면 다시 불러야 한다.
        도커라면 jar 를 이미지에 넣고 환경변수를 Dockerfile 에 박아 두는
        편이 낫다 — 그때는 이 함수 대신 `use_converter()` 를 쓴다.
    비고:
        `hwp2hwpx` 는 fat jar 가 아니라서 hwplib·hwpxlib 도 클래스패스에
        있어야 한다. 셋 중 하나만 빠져도 NoClassDefFoundError 가 난다.
    """
    import urllib.request

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # ① JDK. 실행(java)뿐 아니라 컴파일(javac)도 필요하므로 JRE 가 아니라
    #    JDK 를 받는다 — 아래에서 얇은 CLI 를 직접 컴파일한다.
    if shutil.which("javac") is None:
        if verbose:
            print("JDK 설치 중 — 1~2분 걸립니다...")
        subprocess.call(["apt-get", "-qq", "update"])
        subprocess.call(["apt-get", "-qq", "install", "-y", "default-jdk-headless"])
    if shutil.which("java") is None:
        raise RuntimeError(
            "Java 를 설치하지 못했습니다. 직접 설치해 보세요: "
            "apt-get install -y default-jdk-headless "
            "(노트북에서는 앞에 ! 를 붙입니다)"
        )
    if verbose:
        version = subprocess.run(["java", "-version"], capture_output=True,
                                 text=True).stderr.splitlines()[:1]
        print(f"Java: {version[0] if version else '확인됨'}")

    # ② jar
    for name, path in HWP2HWPX_JARS.items():
        target = dest / f"{name}.jar"
        if target.is_file() and target.stat().st_size > 0:
            if verbose:
                print(f"  이미 있음: {name}.jar")
            continue
        url = f"{MAVEN_BASE}/{path}"
        if verbose:
            print(f"  내려받는 중: {name}.jar")
        try:
            urllib.request.urlretrieve(url, target)
        except Exception as exc:                 # noqa: BLE001 - 원인을 그대로 보여 준다
            raise RuntimeError(
                f"{name}.jar 를 받지 못했습니다 ({url}): {exc}\n"
                "사내망이라면 jar 를 직접 올린 뒤 install_hwp2hwpx 대신 "
                f"{CONVERTER_ENV} 를 직접 설정하세요."
            ) from exc
        if target.stat().st_size < 10_000:
            # 프록시가 오류 페이지를 200 으로 돌려주는 경우가 있다.
            raise RuntimeError(
                f"{name}.jar 가 너무 작습니다({target.stat().st_size}바이트) — "
                "네트워크가 차단됐을 수 있습니다."
            )

    # ③ 실행 스크립트 (README 3줄을 담은 얇은 CLI 를 컴파일)
    classpath = ":".join(str(dest / f"{n}.jar") for n in HWP2HWPX_JARS)
    script = _write_cli(dest, classpath, verbose=verbose)

    # ④ 환경변수
    os.environ[CONVERTER_ENV] = f"{script} {{input}} {{output}}"

    if verbose:
        print(f"\n설치 완료: {dest}")
        print(f"  {CONVERTER_ENV} 설정됨")
        print("  주의: 런타임을 다시 시작하면 이 설정은 사라집니다.")
    return dest


def use_converter(jar_dir: str | Path, *, verbose: bool = True) -> str:
    """이미 받아 둔 jar 로 변환기를 설정한다 (오프라인·사내망용).

    입력: jar_dir — hwp2hwpx·hwplib·hwpxlib jar 가 있는 폴더
    출력: 설정된 명령 문자열
    예외: jar 가 없거나 CLI 를 만들지 못하면 RuntimeError
    비고:
        `install_hwp2hwpx` 는 Maven Central 에서 내려받는다. 사내망이나
        Colab 외 환경에서는 그 주소가 막혀 있을 수 있으므로, jar 를 미리
        올려 두고 이 함수로 연결한다.

        jar 이름은 `hwp2hwpx*.jar` 처럼 접두만 맞으면 된다 — 버전이 붙은
        파일명을 그대로 쓸 수 있게 하기 위함이다.
    """
    folder = Path(jar_dir).expanduser().resolve()
    if not folder.is_dir():
        raise RuntimeError(f"폴더를 찾을 수 없습니다: {folder}")

    found: dict[str, Path] = {}
    for name in HWP2HWPX_JARS:
        matches = sorted(folder.glob(f"{name}*.jar"))
        if not matches:
            raise RuntimeError(
                f"{name} jar 를 {folder} 에서 찾지 못했습니다. "
                f"필요한 것: {', '.join(HWP2HWPX_JARS)}"
            )
        found[name] = matches[0]

    classpath = ":".join(str(p) for p in found.values())
    _write_cli(folder, classpath, verbose=verbose)
    command = f"{folder / 'convert.sh'} {{input}} {{output}}"
    os.environ[CONVERTER_ENV] = command
    if verbose:
        for name, path in found.items():
            print(f"  {name}: {path.name}")
        print(f"\n{CONVERTER_ENV} 설정됨")
    return command


def _write_cli(dest: Path, classpath: str, *, verbose: bool = True) -> Path:
    """README 의 3줄 사용법을 담은 CLI 를 컴파일한다.

    입력: dest — 결과를 둘 폴더, classpath — jar 클래스패스
    출력: 실행 스크립트 경로
    예외: javac 가 없거나 컴파일이 실패하면 RuntimeError
    비고:
        hwp2hwpx 는 **라이브러리**라 main 메서드가 없다. `java -jar` 로는
        실행되지 않으므로 진입점을 직접 만든다. 클래스 이름을 추측하는
        것보다 확실하다.
    """
    source = dest / "Hwp2HwpxCli.java"
    source.write_text(
        "import kr.dogfoot.hwplib.object.HWPFile;\n"
        "import kr.dogfoot.hwplib.reader.HWPReader;\n"
        "import kr.dogfoot.hwpxlib.object.HWPXFile;\n"
        "import kr.dogfoot.hwpxlib.writer.HWPXWriter;\n"
        "import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;\n"
        "\n"
        "public class Hwp2HwpxCli {\n"
        "    public static void main(String[] args) throws Exception {\n"
        "        if (args.length < 2) {\n"
        '            System.err.println("사용법: Hwp2HwpxCli <입력.hwp> <출력.hwpx>");\n'
        "            System.exit(2);\n"
        "        }\n"
        "        HWPFile from = HWPReader.fromFile(args[0]);\n"
        "        HWPXFile to = Hwp2Hwpx.toHWPX(from);\n"
        "        HWPXWriter.toFilepath(to, args[1]);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    if shutil.which("javac") is None:
        raise RuntimeError(
            "javac 가 없어 CLI 를 만들지 못했습니다. "
            "JDK 를 설치하세요: apt-get install -y default-jdk-headless "
            "(노트북에서는 앞에 ! 를 붙입니다)"
        )

    if verbose:
        print("변환기 CLI 컴파일 중...")
    compiled = subprocess.run(
        ["javac", "-cp", classpath, "-d", str(dest), str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if compiled.returncode != 0:
        tail = "\n".join((compiled.stderr or "").splitlines()[-10:])
        raise RuntimeError(f"CLI 컴파일 실패:\n{tail}")

    script = dest / "convert.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'exec java -cp "{classpath}:{dest}" Hwp2HwpxCli "$1" "$2"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def check_converter(sample: str | Path | None = None) -> bool:
    """변환기가 실제로 동작하는지 확인한다.

    입력: sample — 시험할 .hwp 경로. 생략하면 설정 여부만 본다
    출력: 쓸 수 있으면 True
    비고:
        `install_hwp2hwpx` 가 성공해도 진입점 클래스 이름이 다르면 실행에서
        실패한다. 실제 파일 하나로 끝까지 돌려 보는 편이 확실하다.
    """
    if not is_available():
        print("변환기가 설정되지 않았습니다 — use_converter('/opt/hwp2hwpx') 또는 install_converter() 를 실행하세요.")
        return False
    print(f"명령: {converter_command()}")

    if sample is None:
        print("설정은 되어 있습니다. 실제 동작은 sample= 로 .hwp 를 주면 확인합니다.")
        return True

    import time

    started = time.perf_counter()
    try:
        out = convert(sample)
    except Exception as exc:                     # noqa: BLE001 - 사용자에게 원인을 보여 준다
        print(f"실패: {exc}")
        return False
    elapsed = time.perf_counter() - started
    print(f"성공: {out.name} ({out.stat().st_size / 1024:,.0f} KB · {elapsed:.1f}초)")
    return True
