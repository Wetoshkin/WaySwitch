use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Arc;

const MAX_BYTES: usize = 64 * 1024 * 1024;
struct Reader<'a> {
    bytes: &'a [u8],
    pos: usize,
}
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], ()> {
        let end = self.pos.checked_add(n).ok_or(())?;
        let value = self.bytes.get(self.pos..end).ok_or(())?;
        self.pos = end;
        Ok(value)
    }
    fn count(&mut self) -> Result<usize, ()> {
        let n = u32::from_le_bytes(self.take(4)?.try_into().map_err(|_| ())?) as usize;
        if n > 1_000_000 {
            return Err(());
        }
        Ok(n)
    }
    fn string(&mut self) -> Result<String, ()> {
        let n = self.count()?;
        if n > 65536 {
            return Err(());
        }
        Ok(std::str::from_utf8(self.take(n)?)
            .map_err(|_| ())?
            .to_owned())
    }
    fn strings(&mut self) -> Result<Vec<String>, ()> {
        let n = self.count()?;
        if n > self.bytes.len().saturating_sub(self.pos) / 4 {
            return Err(());
        }
        (0..n).map(|_| self.string()).collect()
    }
    fn number(&mut self) -> Result<f64, ()> {
        let n = f64::from_le_bytes(self.take(8)?.try_into().map_err(|_| ())?);
        if n.is_finite() {
            Ok(n)
        } else {
            Err(())
        }
    }
    fn map(&mut self) -> Result<HashMap<String, f64>, ()> {
        let n = self.count()?;
        if n > self.bytes.len().saturating_sub(self.pos) / 12 {
            return Err(());
        }
        let mut out = HashMap::new();
        for _ in 0..n {
            if out.insert(self.string()?, self.number()?).is_some() {
                return Err(());
            }
        }
        Ok(out)
    }
    fn finish(self) -> Result<(), ()> {
        if self.pos == self.bytes.len() {
            Ok(())
        } else {
            Err(())
        }
    }
}

pub struct Model {
    ru: bool,
    words: Vec<String>,
    folded: Vec<String>,
    endings: HashSet<String>,
    top: Arc<HashSet<String>>,
    contractions: Arc<HashSet<String>>,
    tri: Arc<HashMap<String, f64>>,
    ctx: Arc<HashMap<String, f64>>,
    floor: f64,
}
impl Model {
    fn index(&mut self) {
        self.words.sort_unstable();
        self.words.dedup();
        self.folded = if self.ru {
            let mut v: Vec<_> = self.words.iter().map(|w| w.replace('ё', "е")).collect();
            v.sort_unstable();
            v.dedup();
            v
        } else {
            Vec::new()
        };
        let mut counts = HashMap::new();
        for w in &self.words {
            let chars: Vec<_> = w.chars().collect();
            if chars.len() >= 3 {
                *counts
                    .entry(chars[chars.len() - 2..].iter().collect::<String>())
                    .or_insert(0) += 1;
            }
        }
        self.endings = counts
            .into_iter()
            .filter(|(_, n)| *n >= 3)
            .map(|(s, _)| s)
            .collect();
    }
    fn read(bytes: &[u8]) -> Result<Self, ()> {
        let mut r = Reader { bytes, pos: 0 };
        if r.take(8)? != b"WSMODEL1" {
            return Err(());
        }
        let ru = match r.string()?.as_str() {
            "ru" => true,
            "en" => false,
            _ => return Err(()),
        };
        let _alphabet = r.string()?;
        let words = r.strings()?;
        let top = Arc::new(r.strings()?.into_iter().collect());
        let contractions = Arc::new(r.strings()?.into_iter().collect());
        let tri = Arc::new(r.map()?);
        let ctx = Arc::new(r.map()?);
        let floor = r.number()?;
        r.finish()?;
        let mut m = Self {
            ru,
            words,
            folded: Vec::new(),
            endings: HashSet::new(),
            top,
            contractions,
            tri,
            ctx,
            floor,
        };
        m.index();
        Ok(m)
    }
    fn query(&self, op: u32, text: &str, limit: u32) -> Result<i64, ()> {
        let words = if self.ru { &self.folded } else { &self.words };
        let has = |v: &[String]| v.binary_search_by(|w| w.as_str().cmp(text)).is_ok() as i64;
        Ok(match op {
            1 => has(&self.words),
            2 => has(words),
            3 => self.top.contains(text) as i64,
            4 => self.endings.contains(text) as i64,
            6 => self.contractions.contains(text) as i64,
            5 => {
                if text.is_empty() {
                    return Ok(0);
                }
                let i = words.partition_point(|w| w.as_str() < text);
                words[i..]
                    .iter()
                    .take(limit as usize)
                    .take_while(|w| w.starts_with(text))
                    .count() as i64
            }
            7 => self.words.len() as i64,
            _ => return Err(()),
        })
    }
    fn score(&self, text: &str) -> f64 {
        let s: Vec<_> = ['^', '^']
            .into_iter()
            .chain(text.chars())
            .chain(['$'])
            .collect();
        let mut total = 0.0;
        for i in 2..s.len() {
            let abc: String = s[i - 2..=i].iter().collect();
            total += match self.tri.get(&abc) {
                Some(n) => *n,
                None => *self
                    .ctx
                    .get(&s[i - 2..i].iter().collect::<String>())
                    .unwrap_or(&self.floor),
            };
        }
        total / (s.len() - 2) as f64
    }
}

unsafe fn bytes<'a>(p: *const u8, n: usize) -> Result<&'a [u8], ()> {
    if n > MAX_BYTES || (p.is_null() && n != 0) {
        return Err(());
    }
    Ok(if n == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(p, n)
    })
}

#[no_mangle]
pub unsafe extern "C" fn ws_load(p: *const u8, n: usize) -> *mut Model {
    catch_unwind(|| Model::read(bytes(p, n)?).map(|m| Box::into_raw(Box::new(m))))
        .ok()
        .and_then(Result::ok)
        .unwrap_or(std::ptr::null_mut())
}
#[no_mangle]
pub unsafe extern "C" fn ws_free(p: *mut Model) {
    if !p.is_null() {
        drop(Box::from_raw(p));
    }
}
#[no_mangle]
pub unsafe extern "C" fn ws_query(
    m: *const Model,
    op: u32,
    p: *const u8,
    n: usize,
    limit: u32,
) -> i64 {
    catch_unwind(|| {
        if n > 65536 {
            return Err(());
        }
        let m = m.as_ref().ok_or(())?;
        let text = std::str::from_utf8(bytes(p, n)?).map_err(|_| ())?;
        m.query(op, text, limit)
    })
    .ok()
    .and_then(Result::ok)
    .unwrap_or(-1)
}
#[no_mangle]
pub unsafe extern "C" fn ws_score(m: *const Model, p: *const u8, n: usize) -> f64 {
    catch_unwind(|| {
        if n > 65536 {
            return Err(());
        }
        let m = m.as_ref().ok_or(())?;
        let text = std::str::from_utf8(bytes(p, n)?).map_err(|_| ())?;
        Ok::<f64, ()>(m.score(text))
    })
    .ok()
    .and_then(Result::ok)
    .unwrap_or(f64::NAN)
}
#[no_mangle]
pub unsafe extern "C" fn ws_extend(m: *const Model, p: *const u8, n: usize) -> *mut Model {
    catch_unwind(AssertUnwindSafe(|| {
        let m = m.as_ref().ok_or(())?;
        let mut r = Reader {
            bytes: bytes(p, n)?,
            pos: 0,
        };
        let mut words = m.words.clone();
        words.extend(r.strings()?);
        r.finish()?;
        let mut result = Model {
            ru: m.ru,
            words,
            folded: Vec::new(),
            endings: HashSet::new(),
            top: m.top.clone(),
            contractions: m.contractions.clone(),
            tri: m.tri.clone(),
            ctx: m.ctx.clone(),
            floor: m.floor,
        };
        result.index();
        Ok::<_, ()>(Box::into_raw(Box::new(result)))
    }))
    .ok()
    .and_then(Result::ok)
    .unwrap_or(std::ptr::null_mut())
}
