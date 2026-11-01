// gs-gyro: dump a camera file's motion metadata for gigsteady, for a time range only (bounded memory).
//   <prefix>.imu    f64 rows [timestamp_ms, gx, gy, gz, ax, ay, az]  normalised IMU (deg/s, m/s^2; NaN if missing)
//   <prefix>.ibis   f64 rows [frame_ts_ms, table, t, x, y, z]  Sony IBIS tables per frame: table 1 = imager shift
//                   (e40f; x, y [nm]), table 2 = shift + roll (e450; z = roll angle / scale)
//   <prefix>.frames f64 rows [frame_ts_ms, exposure_ms, readout_ms, first_frame_ts_ms]  (NaN if missing)
// Usage: gs-gyro <video> <prefix> [--range <t0_ms> <t1_ms>] [--orientation XYZ]
// Ported from pit-stabilise/vendor/gyrodump. Without --range it parses the whole file, which holds every frame's
// metadata in memory (~24 GB for a 100-min FX30 file): gigsteady always passes a range.
use std::io::Write;
use std::sync::{atomic::AtomicBool, Arc};
use telemetry_parser::tags_impl::{GetWithType, GroupId, TagId, TimeVector3};
use telemetry_parser::{util, Input, InputOptions};

fn write_row(out: &mut impl Write, row: &[f64]) {
    for v in row {
        out.write_all(&v.to_le_bytes()).unwrap();
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: gs-gyro <video> <prefix> [--range <t0_ms> <t1_ms>] [--orientation XYZ]");
        std::process::exit(2);
    }
    let mut options = InputOptions::default();
    let mut orientation = None;
    let mut i = 3;
    while i < args.len() {
        match args[i].as_str() {
            "--range" => {
                options.time_range_ms = Some((args[i + 1].parse().expect("t0_ms"), args[i + 2].parse().expect("t1_ms")));
                i += 3;
            }
            "--orientation" => {
                orientation = Some(args[i + 1].clone());
                i += 2;
            }
            a => panic!("unknown argument {a}"),
        }
    }
    let mut stream = std::fs::File::open(&args[1]).expect("open video");
    let size = stream.metadata().unwrap().len() as usize;
    let input = Input::from_stream_with_options(&mut stream, size, &args[1], |_| (), Arc::new(AtomicBool::new(false)), options)
        .expect("parse");
    eprintln!("camera: {} {}", input.camera_type(), input.camera_model().map(|s| s.as_str()).unwrap_or(""));

    let prefix = &args[2];
    let imu = util::normalized_imu(&input, orientation).expect("imu");
    let mut out = std::io::BufWriter::new(std::fs::File::create(format!("{prefix}.imu")).expect("create out"));
    let nan = [f64::NAN; 3];
    for s in &imu {
        let g = s.gyro.unwrap_or(nan);
        let a = s.accl.unwrap_or(nan);
        write_row(&mut out, &[s.timestamp_ms, g[0], g[1], g[2], a[0], a[1], a[2]]);
    }
    out.flush().unwrap();

    let mut ibis = std::io::BufWriter::new(std::fs::File::create(format!("{prefix}.ibis")).unwrap());
    let mut frames = std::io::BufWriter::new(std::fs::File::create(format!("{prefix}.frames")).unwrap());
    let (mut n_ibis, mut n_frames) = (0usize, 0usize);
    if let Some(samples) = input.samples.as_ref() {
        for info in samples {
            let Some(map) = info.tag_map.as_ref() else { continue };
            if let Some(m) = map.get(&GroupId::IBIS) {
                for (table, id) in [(1.0, TagId::Data), (2.0, TagId::Data2)] {
                    let v: Option<&Vec<TimeVector3<i32>>> = m.get_t(id);
                    for e in v.into_iter().flatten() {
                        write_row(&mut ibis, &[info.timestamp_ms, table, e.t as f64, e.x as f64, e.y as f64, e.z as f64]);
                        n_ibis += 1;
                    }
                }
            }
            if let Some(m) = map.get(&GroupId::Imager) {
                let g = |id: TagId| -> f64 { let v: Option<&f64> = m.get_t(id); v.copied().unwrap_or(f64::NAN) };
                write_row(&mut frames, &[info.timestamp_ms, g(TagId::ExposureTime), g(TagId::FrameReadoutTime), g(TagId::FirstFrameTimestamp)]);
                n_frames += 1;
            }
        }
    }
    ibis.flush().unwrap();
    frames.flush().unwrap();
    eprintln!("samples: {}  ibis rows: {}  frame rows: {}", imu.len(), n_ibis, n_frames);
}
