{
  description = "Nix development shell for TangPrimer-20K riscv-mini";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    gowin-wrapper = {
      url = "git+https://github.com/bearice/gowin-ide-wrapper.git?ref=main";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { nixpkgs, gowin-wrapper, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      gowin = gowin-wrapper.packages.${system}.default;
      jdk8 = pkgs.jdk8_headless;

      sbtLauncher = pkgs.stdenvNoCC.mkDerivation {
        pname = "sbt-launch";
        version = "1.9.7";
        src = pkgs.fetchurl {
          url = "https://repo.maven.apache.org/maven2/org/scala-sbt/sbt-launch/1.9.7/sbt-launch-1.9.7.jar";
          hash = "sha256-UkTpMtuR6mvU0f+3+bx8RRwnftVSuU8magPAkgClbtQ=";
        };
        dontUnpack = true;
        dontBuild = true;
        installPhase = ''
          install -Dm644 "$src" "$out/share/java/sbt-launch.jar"
        '';
      };

      riscvGcc = pkgs.stdenvNoCC.mkDerivation {
        pname = "xpack-riscv-none-elf-gcc";
        version = "15.2.0-1";
        src = pkgs.fetchurl {
          url = "https://github.com/xpack-dev-tools/riscv-none-elf-gcc-xpack/releases/download/v15.2.0-1/xpack-riscv-none-elf-gcc-15.2.0-1-linux-x64.tar.gz";
          hash = "sha256-qqqAYMkUhRo+XuG6gsw9b4CXL5BjigXG6COjdVejN1g=";
        };
        nativeBuildInputs = [ pkgs.gnutar pkgs.gzip ];
        dontUnpack = true;
        dontBuild = true;
        installPhase = ''
          mkdir -p "$out"
          tar --no-same-owner --strip-components=1 -xzf "$src" -C "$out"
        '';
      };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.coreutils
          pkgs.git
          pkgs.gnumake
          pkgs.openfpgaloader
          pkgs.uv
          jdk8
          sbtLauncher
          gowin
          riscvGcc
        ];

        shellHook = ''
          export PYTHONUTF8=1
          export PYTHONUNBUFFERED=1
          export LITEX_ENV_CC_TRIPLE=riscv-none-elf
          export JAVA_HOME="${jdk8}/lib/openjdk"
          export SBT_LAUNCH_JAR="${sbtLauncher}/share/java/sbt-launch.jar"
          export GOWIN_HOME="''${GOWIN_HOME:-$HOME/Gowin_V1.9.12.04_linux}"
          export GOWIN_ROOT="$GOWIN_HOME"
          export UV_PYTHON_INSTALL_DIR="''${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}"
          export PATH="${gowin}/bin:$GOWIN_HOME/Programmer/bin:$PATH"
          if [ -z "''${DISPLAY:-}" ] && [ -z "''${WAYLAND_DISPLAY:-}" ] && [ -z "''${QT_QPA_PLATFORM:-}" ]; then
            export QT_QPA_PLATFORM=offscreen
          fi

          python_version="$(tr -d '\r\n' < .python-version)"
          if ! uv python find --managed-python --system "$python_version" >/dev/null 2>&1; then
            uv python install --no-bin "$python_version" || exit $?
          fi
          managed_python="$(uv python find --managed-python --system "$python_version")" || exit $?
          venv_python_version="$(.venv/bin/python -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
          venv_managed_version="$(cat .venv/.managed-python-version 2>/dev/null || true)"

          if [ -x "$GOWIN_HOME/IDE/bin/gw_sh" ]; then
            :
          else
            printf 'Gowin IDE not found under %s; set GOWIN_HOME to its extracted directory.\n' "$GOWIN_HOME" >&2
          fi

          if [ "$venv_python_version" != "$python_version" ] || [ "$venv_managed_version" != "$python_version" ]; then
            uv venv --clear --python "$managed_python" .venv || exit $?
            printf '%s\n' "$python_version" > .venv/.managed-python-version
          fi

          lock_hash="$(sha256sum requirements.lock | cut -d' ' -f1)"
          installed_lock="$(cat .venv/.requirements.lock.sha256 2>/dev/null || true)"
          if [ "$lock_hash" != "$installed_lock" ]; then
            uv pip sync --python .venv/bin/python requirements.lock || exit $?
            printf '%s\n' "$lock_hash" > .venv/.requirements.lock.sha256
          fi

          source .venv/bin/activate
          export VEXRISCV_SOURCE="$(python -c 'from pathlib import Path; import pythondata_cpu_vexriscv as p; print(Path(p.data_location) / "ext/VexRiscv")')"
          sbt() { java -jar "$SBT_LAUNCH_JAR" "$@"; }
          if [ -x "$GOWIN_HOME/IDE/bin/gw_sh" ]; then
            python scripts/tools/configure_tools.py --gowin-bin "${gowin}/bin" --riscv-bin "${riscvGcc}/bin" || exit $?
          else
            python scripts/tools/configure_tools.py --riscv-bin "${riscvGcc}/bin" || exit $?
          fi
        '';
      };
    };
}
